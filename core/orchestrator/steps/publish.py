import os
import re
import tempfile
from pathlib import Path
from subprocess import run
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

from core.contracts.enums import PipelineStage
from core.orchestrator.models import RunModel, StageResult, StageStatus
from infra.repo_worker.git_utils import GitService

from core.orchestrator.steps.base import PipelineStep, StepRuntime

class PublishStep(PipelineStep):
    stage = PipelineStage.PUBLISH

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _first_non_empty_text(*values: Any) -> str:
        for value in values:
            normalized = PublishStep._normalize_text(value)
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _derive_head_branch(context: dict[str, Any]) -> str:
        head = PublishStep._first_non_empty_text(
            context.get("head_branch"),
            context.get("pr_head"),
        )
        if head:
            return head
        issue_number = context.get("issue_number")
        if isinstance(issue_number, int):
            return f"autopr/issue-{issue_number}"
        run_id_value = context.get("run_id")
        if isinstance(run_id_value, UUID):
            run_id = run_id_value.hex
        else:
            run_id = PublishStep._normalize_text(run_id_value)
        run_suffix = run_id[:8] if run_id else "manual"
        return f"autopr/run-{run_suffix}"

    @staticmethod
    def _derive_commit_message(context: dict[str, Any]) -> str:
        explicit = PublishStep._normalize_text(context.get("commit_message"))
        if explicit:
            return explicit
        issue_number = context.get("issue_number")
        if isinstance(issue_number, int):
            return f"fix: resolve issue #{issue_number}"
        return "chore: apply AutoPR changes"

    @staticmethod
    def _with_tokenized_https_clone_url(clone_url: str, token: str) -> str:
        if not token:
            return clone_url
        parsed = urlsplit(clone_url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            return clone_url
        if "@" in parsed.netloc:
            return clone_url
        # Inject token only for cloning; never rewrite ssh or already-authenticated URLs.
        safe_token = quote(token, safe="")
        netloc = f"x-access-token:{safe_token}@{parsed.netloc}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    @staticmethod
    def _sanitize_error_text(error_text: str, context: dict[str, Any]) -> str:
        text = error_text if isinstance(error_text, str) else ""
        secrets = [
            PublishStep._normalize_text(context.get("github_token")),
            PublishStep._normalize_text(os.environ.get("GITHUB_TOKEN")),
            PublishStep._normalize_text(os.environ.get("GH_TOKEN")),
        ]
        for secret in secrets:
            if secret:
                text = text.replace(secret, "***REDACTED***")
        text = re.sub(r"https://[^@\s]+@github\.com", "https://***REDACTED***@github.com", text)
        text = re.sub(r"github_pat_[A-Za-z0-9_]+", "***REDACTED***", text)
        return text

    @staticmethod
    def _resolve_api_token_from_git_credentials(workspace_path: Path, repository: str) -> str:
        try:
            response = run(
                ["git", "credential", "fill"],
                cwd=workspace_path,
                input=f"url=https://github.com/{repository}.git\n\n",
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except Exception:
            return ""
        if response.returncode != 0:
            return ""
        credentials: dict[str, str] = {}
        for line in (response.stdout or "").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            credentials[key.strip()] = value.strip()
        password = credentials.get("password")
        return password if isinstance(password, str) else ""

    @staticmethod
    def _configure_git_identity(git: GitService, context: dict[str, Any]) -> None:
        author_name = PublishStep._first_non_empty_text(
            context.get("git_author_name"),
            os.environ.get("GIT_AUTHOR_NAME"),
            os.environ.get("GIT_COMMITTER_NAME"),
            "AutoPR Bot",
        )
        author_email = PublishStep._first_non_empty_text(
            context.get("git_author_email"),
            os.environ.get("GIT_AUTHOR_EMAIL"),
            os.environ.get("GIT_COMMITTER_EMAIL"),
            "autopr-bot@users.noreply.github.com",
        )
        git.set_config("user.name", author_name)
        git.set_config("user.email", author_email)

    @staticmethod
    def _write_generated_files(workspace_path: Path, files: dict[str, str]) -> list[str]:
        workspace_root = workspace_path.resolve()
        written_files: list[str] = []
        for relative_path, content in files.items():
            normalized_path = relative_path.strip()
            if not normalized_path:
                continue
            path_obj = Path(normalized_path)
            if path_obj.is_absolute():
                raise ValueError(f"Generated file path must be relative: {normalized_path}")
            destination = (workspace_root / path_obj).resolve()
            destination.relative_to(workspace_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            written_files.append(normalized_path)
        return written_files

    def _resolve_publish_workspace(
        self,
        context: dict[str, Any],
        repository: str,
        base_branch: str,
    ) -> tuple[GitService, Path, bool]:
        repo_path_hint = self._normalize_text(context.get("repo_path") or context.get("local_repo_path"))
        if repo_path_hint:
            candidate = Path(repo_path_hint).expanduser().resolve()
            local_git = GitService(candidate)
            try:
                local_git.current_branch()
                return local_git, candidate, False
            except Exception:
                pass

        # Fall back to an isolated clone when no valid local workspace is provided.
        clone_url = self._normalize_text(context.get("repository_clone_url")) or f"https://github.com/{repository}.git"
        token = self._first_non_empty_text(
            context.get("github_token"),
            os.environ.get("GITHUB_TOKEN"),
            os.environ.get("GH_TOKEN"),
        )
        tokenized_clone_url = self._with_tokenized_https_clone_url(clone_url, token)

        temp_dir = Path(tempfile.mkdtemp(prefix="autopr-publish-"))
        clone_candidates = [tokenized_clone_url] if tokenized_clone_url != clone_url else [clone_url]
        if clone_url not in clone_candidates:
            clone_candidates.append(clone_url)

        clone_errors: list[Exception] = []
        git: GitService | None = None
        for candidate_url in clone_candidates:
            try:
                try:
                    git = GitService.clone(candidate_url, temp_dir, branch=base_branch)
                except Exception:
                    git = GitService.clone(candidate_url, temp_dir)
                if git is not None:
                    break
            except Exception as exc:
                clone_errors.append(exc)

        if git is None:
            if clone_errors:
                raise clone_errors[-1]
            raise RuntimeError("Failed to clone repository for publish step")

        return git, temp_dir, True

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        repository = self._normalize_text(context.get("repository"))
        if not repository:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "Publish blocked: repository is missing."},
            )

        execute_remote = bool(context.get("execute_remote_actions", False))
        if not execute_remote:
            return StageResult(
                stage=self.stage,
                status=StageStatus.NEEDS_REVIEW,
                notes={"reason": "Publish skipped: execute_remote_actions is False."},
            )

        coding_output = context.get("coding_output", {})
        if not isinstance(coding_output, dict):
            coding_output = {}

        files_map = coding_output.get("files_map", {})
        tests_map = coding_output.get("tests_map", {})
        legacy_files = coding_output.get("files", {})
        if not isinstance(files_map, dict):
            files_map = {}
        if not isinstance(tests_map, dict):
            tests_map = {}
        if not isinstance(legacy_files, dict):
            legacy_files = {}

        files_payload = dict(files_map)
        files_payload.update(tests_map)
        if not files_payload:
            files_payload = dict(legacy_files)
        if not files_payload:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "Publish blocked: no generated files available."},
            )

        base_branch = self._normalize_text(context.get("base_branch") or context.get("pr_base")) or "main"
        head_branch = self._derive_head_branch(context)
        commit_message = self._derive_commit_message(context)
        remote_name = self._normalize_text(context.get("git_remote")) or "origin"
        pr_auth_source = "environment_or_context"

        try:
            git, workspace_path, used_temp_workspace = self._resolve_publish_workspace(context, repository, base_branch)
            self._configure_git_identity(git, context)
            git.ensure_checkout_branch(base_branch, remote=remote_name, base_branch=base_branch)
            try:
                git.pull(remote=remote_name, branch=base_branch, rebase=False)
            except Exception:
                pass
            git.ensure_checkout_branch(head_branch, remote=remote_name, base_branch=base_branch)

            # Materialize generated artifacts exactly as produced by coding stage.
            written_files = self._write_generated_files(workspace_path, files_payload)
            if not written_files:
                raise ValueError("No valid generated files to write")

            git.add(*written_files)
            if not git.status(short=True).strip():
                return StageResult(
                    stage=self.stage,
                    status=StageStatus.BLOCKED,
                    notes={
                        "reason": "Publish blocked: no working tree changes after applying files.",
                        "workspace_path": str(workspace_path),
                        "head_branch": head_branch,
                        "used_temp_workspace": used_temp_workspace,
                    },
                )

            commit_output = git.commit(commit_message)
            push_output = git.push(remote=remote_name, branch=head_branch, set_upstream=True)
            head_sha = git.head_sha()

            resolved_api_token = self._resolve_api_token_from_git_credentials(workspace_path, repository)
            if resolved_api_token:
                # Prefer the git credential token for subsequent API calls in this run.
                context["github_token"] = resolved_api_token
                pr_auth_source = "git_credential"

        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={
                    "reason": "Publish failed.",
                    "error": self._sanitize_error_text(str(exc), context),
                    "head_branch": head_branch,
                    "base_branch": base_branch,
                    "remote": remote_name,
                },
            )

        return StageResult(
            stage=self.stage,
            status=StageStatus.OK,
            outputs={
                "publish_output": f"Published changes to {head_branch}.",
                "head_branch": head_branch,
                "pr_head": head_branch,
                "base_branch": base_branch,
            },
            notes={
                "head_branch": head_branch,
                "base_branch": base_branch,
                "remote": remote_name,
                "workspace_path": str(workspace_path),
                "used_temp_workspace": used_temp_workspace,
                "files_written": written_files,
                "commit_output": commit_output,
                "push_output": push_output,
                "head_sha": head_sha,
                "pr_auth_source": pr_auth_source,
            },
        )
