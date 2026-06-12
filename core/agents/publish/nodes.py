import os
import re
import tempfile
from pathlib import Path
from subprocess import run
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID
from core.orchestrator.models import StageStatus
from infra.repo_worker.git_utils import GitService
from observability.tracing import traced, langgraph_node_attrs


def _derive_head_branch(context: dict[str, Any]) -> str:
    head = context.get("head_branch") or context.get("pr_head")
    if head:
        return head

    # Derive from issue number if available
    issue_number = context.get("issue_number")
    if isinstance(issue_number, int):
        return f"autopr/issue-{issue_number}"

    # Derive from run id if available
    run_id_value = context.get("run_id")
    if isinstance(run_id_value, UUID):
        run_id = run_id_value.hex
    else:
        run_id = str(run_id_value)

    # Use first 8 chars of run id as suffix
    run_suffix = run_id[:8] if run_id else "manual"
    return f"autopr/run-{run_suffix}"


def _derive_commit_message(context: dict[str, Any]) -> str:
    explicit = context.get("commit_message")
    if explicit:
        return explicit

    issue_number = context.get("issue_number")
    if isinstance(issue_number, int):
        return f"fix: resolve issue #{issue_number}"

    return "chore: apply AutoPR changes"


def _with_tokenized_https_clone_url(clone_url: str, token: str) -> str:
    if not token:
        return clone_url

    parsed = urlsplit(clone_url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return clone_url

    if "@" in parsed.netloc:
        return clone_url

    safe_token = quote(token, safe="")
    netloc = f"x-access-token:{safe_token}@{parsed.netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _sanitize_error_text(error_text: str, context: dict[str, Any]) -> str:
    text = error_text if isinstance(error_text, str) else ""
    secrets = [
        context.get("github_token"),
        os.environ.get("GITHUB_TOKEN"),
        os.environ.get("GH_TOKEN"),
    ]

    for secret in secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")

    text = re.sub(r"https://[^@\s]+@github\.com", "https://***REDACTED***@github.com", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]+", "***REDACTED***", text)

    return text


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


def _configure_git_identity(git: GitService, context: dict[str, Any]) -> None:
    author_name = os.environ.get("GIT_COMMITTER_NAME") or "AutoPR Bot"
    author_email = os.environ.get("GIT_COMMITTER_EMAIL") or "autopr-bot@users.noreply.github.com"

    git.set_config("user.name", author_name)
    git.set_config("user.email", author_email)


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
    context: dict[str, Any], repository: str, base_branch: str
) -> tuple[GitService, Path, bool]:
    repo_path_hint = context.get("repo_path") or context.get("local_repo_path")
    if repo_path_hint:
        candidate = Path(repo_path_hint).expanduser().resolve()
        local_git = GitService(candidate)
        try:
            local_git.current_branch()
            return local_git, candidate, False
        except Exception:
            pass

    clone_url = context.get("repository_clone_url") or f"https://github.com/{repository}.git"
    token = (
        context.get("github_token")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    )
    tokenized_clone_url = _with_tokenized_https_clone_url(clone_url, token)
    temp_dir = Path(tempfile.mkdtemp(prefix="autopr-publish-"))
    clone_candidates: list[str] = [tokenized_clone_url]
    if clone_url != tokenized_clone_url:
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


def _block_publish_failed(state: dict[str, Any], exc: Exception) -> dict[str, Any]:
    context = state.get("context")
    if not isinstance(context, dict):
        context = {}

    state["status"] = StageStatus.BLOCKED
    state["notes"] = {
        "reason": "Publish failed.",
        "error": _sanitize_error_text(str(exc), context),
        "head_branch": state.get("head_branch"),
        "base_branch": state.get("base_branch"),
        "remote": state.get("remote"),
    }

    return state


@traced(
    "publish_step.prepare",
    attributes=langgraph_node_attrs("publish", "prepare"),
)
def prepare(state: dict[str, Any]) -> dict[str, Any]:
    """
    Validate the publish context and resolve the repository, branches, commit message, and files to publish.

    Args:
        state: A dictionary containing the current state of the publish process, including the publish context.
        ```
        {
            "context": {
                "repository": "owner/repo",
                "execute_remote_actions": True,
                "coding_output": {"files_map": {...}, "tests_map": {...}},
                // other context fields...
            },
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the resolved publish parameters (repository, branches, commit message, files payload) and status set to OK when inputs are valid, or BLOCKED/NEEDS_REVIEW with notes when the publish cannot proceed.
    """

    # Get repository from the context
    context = state.get("context")
    if not isinstance(context, dict):
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Publish blocked: invalid context payload."}
        return state

    repository = context.get("repository")
    if not repository:
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Publish blocked: repository is missing."}
        return state

    # Check if remote actions are enabled. If not, block the publish step with notes
    if not bool(context.get("execute_remote_actions", False)):
        state["status"] = StageStatus.NEEDS_REVIEW
        state["notes"] = {"reason": "Publish skipped: execute_remote_actions is False."}
        return state

    # Get files (files_map, tests_map, legacy_files) from the context
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

    # Normalize the files payload
    typed_files_payload: dict[str, str] = {}
    for path, content in files_payload.items():
        if isinstance(path, str) and isinstance(content, str):
            typed_files_payload[path] = content

    # Check if there are any files to publish
    if not typed_files_payload:
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Publish blocked: no generated files available."}
        return state

    state["repository"] = repository
    state["base_branch"] = context.get("base_branch") or context.get("pr_base") or "main"
    state["head_branch"] = _derive_head_branch(context)
    state["commit_message"] = _derive_commit_message(context)
    state["remote"] = context.get("git_remote") or "origin"
    state["files_payload"] = typed_files_payload
    state["pr_auth_source"] = "environment_or_context"
    state["status"] = StageStatus.OK
    state["notes"] = {}

    return state


@traced(
    "publish_step.resolve_workspace",
    attributes=langgraph_node_attrs("publish", "resolve_workspace"),
)
def resolve_workspace(state: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve (or clone) the git workspace where the generated files will be applied.

    Args:
        state: A dictionary containing the current state of the publish process, including the publish context and resolved repository and base branch from the prepare node.
        ```
        {
            "status": StageStatus.OK,
            "context": {...},
            "repository": "owner/repo",
            "base_branch": "main",
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the git service, workspace path, and temp-workspace flag, or BLOCKED with notes when the workspace cannot be resolved.
    """

    if state.get("status") != StageStatus.OK:
        return state

    context = state.get("context")
    if not isinstance(context, dict):
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Publish blocked: invalid context payload."}
        return state

    try:
        git, workspace_path, used_temp_workspace = _resolve_publish_workspace(
            context=context,
            repository=state.get("repository", ""),
            base_branch=state.get("base_branch") or "main",
        )
    except Exception as exc:
        return _block_publish_failed(state, exc)

    state["git"] = git
    state["workspace_path"] = str(workspace_path)
    state["used_temp_workspace"] = used_temp_workspace

    return state


@traced(
    "publish_step.apply_files",
    attributes=langgraph_node_attrs("publish", "apply_files"),
)
def apply_files(state: dict[str, Any]) -> dict[str, Any]:
    """
    Check out the head branch and write the generated files into the workspace, staging the changes.

    Args:
        state: A dictionary containing the current state of the publish process, including the git service, workspace path, branches, and files payload.
        ```
        {
            "status": StageStatus.OK,
            "git": GitService(...),
            "workspace_path": "/path/to/workspace",
            "base_branch": "main",
            "head_branch": "autopr/...",
            "files_payload": {"path": "content", ...},
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the list of written files, or BLOCKED with notes when the workspace setup is invalid or no working tree changes result.
    """

    if state.get("status") != StageStatus.OK:
        return state

    # Check if the workspace was setup properly
    context = state.get("context")
    git = state.get("git")
    workspace_path_value = state.get("workspace_path")
    if (
        not isinstance(context, dict)
        or not isinstance(git, GitService)
        or not isinstance(workspace_path_value, str)
    ):
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Publish blocked: workspace setup is invalid."}
        return state

    # Get the workspace path, base branch, head branch, remote name, and files payload from the state
    workspace_path = Path(workspace_path_value)
    base_branch = state.get("base_branch") or "main"
    head_branch = state.get("head_branch", "")
    remote_name = state.get("remote") or "origin"
    files_payload = state.get("files_payload")
    if not isinstance(files_payload, dict):
        files_payload = {}

    try:
        _configure_git_identity(git, context)
        # Ensure the base branch exists locally
        git.ensure_checkout_branch(base_branch, remote=remote_name, base_branch=base_branch)

        # Pull the latest changes from the remote base branch
        try:
            git.pull(remote=remote_name, branch=base_branch, rebase=False)
        except Exception:
            pass

        # Ensure the head branch exists locally
        git.ensure_checkout_branch(head_branch, remote=remote_name, base_branch=base_branch)

        # Write the generated files
        written_files = _write_generated_files(workspace_path, files_payload)
        if not written_files:
            raise ValueError("No valid generated files to write")

        # Stage the written files
        git.add(*written_files)

        # Check if there are any changes to commit
        if not git.status(short=True).strip():
            state["status"] = StageStatus.BLOCKED
            state["notes"] = {
                "reason": "Publish blocked: no working tree changes after applying files.",
                "workspace_path": str(workspace_path),
                "head_branch": head_branch,
                "used_temp_workspace": bool(state.get("used_temp_workspace", False)),
            }
            return state

        state["written_files"] = written_files
        return state

    except Exception as exc:
        return _block_publish_failed(state, exc)


@traced(
    "publish_step.commit_push",
    attributes=langgraph_node_attrs("publish", "commit_push"),
)
def commit_push(state: dict[str, Any]) -> dict[str, Any]:
    """
    Commit the staged files and push the head branch to the remote.

    Args:
        state: A dictionary containing the current state of the publish process, including the git service, workspace path, repository, commit message, and head branch.
        ```
        {
            "status": StageStatus.OK,
            "git": GitService(...),
            "workspace_path": "/path/to/workspace",
            "repository": "owner/repo",
            "commit_message": "...",
            "head_branch": "autopr/...",
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the commit output, push output, and head SHA, or BLOCKED with notes when the commit or push fails.
    """

    if state.get("status") != StageStatus.OK:
        return state

    # Check if the workspace was setup properly
    context = state.get("context")
    git = state.get("git")
    workspace_path_value = state.get("workspace_path")
    if (
        not isinstance(context, dict)
        or not isinstance(git, GitService)
        or not isinstance(workspace_path_value, str)
    ):
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Publish blocked: workspace setup is invalid."}
        return state

    # Get the workspace path, repository, commit message, remote name, and head branch from the state
    workspace_path = Path(workspace_path_value)
    repository = state.get("repository", "")
    commit_message = state.get("commit_message") or "feat: add changes"
    remote_name = state.get("remote") or "origin"
    head_branch = state.get("head_branch")
    try:
        # Commit the staged files
        state["commit_output"] = git.commit(commit_message)

        # Push the head branch to the remote
        state["push_output"] = git.push(remote=remote_name, branch=head_branch, set_upstream=True)

        # Get the head SHA
        state["head_sha"] = git.head_sha()

        # Resolve the API token from git credentials
        resolved_api_token = _resolve_api_token_from_git_credentials(workspace_path, repository)
        if resolved_api_token:
            context["github_token"] = resolved_api_token
            state["pr_auth_source"] = "git_credential"
        return state
    except Exception as exc:
        return _block_publish_failed(state, exc)


@traced(
    "publish_step.finalize",
    attributes=langgraph_node_attrs("publish", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    """
    Finalize the publish process by compiling the published branch details and notes into the final output.

    Args:
        state: A dictionary containing the current state of the publish process, including the status, branches, remote, and commit/push results.
        ```
        {
            "status": StageStatus.OK,
            "head_branch": "autopr/...",
            "base_branch": "main",
            "remote": "origin",
            "written_files": [...],
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the final output containing the publish outputs and notes.
    """

    notes = state.get("notes", {})
    if not isinstance(notes, dict):
        notes = {}

    outputs: dict[str, Any] = {}
    if state.get("status") == StageStatus.OK:
        head_branch = state.get("head_branch")
        base_branch = state.get("base_branch")
        remote_name = state.get("remote")
        outputs = {
            "publish_output": f"Published changes to {head_branch}.",
            "head_branch": head_branch,
            "pr_head": head_branch,
            "base_branch": base_branch,
        }
        notes = {
            "head_branch": head_branch,
            "base_branch": base_branch,
            "remote": remote_name,
            "workspace_path": state.get("workspace_path"),
            "used_temp_workspace": bool(state.get("used_temp_workspace", False)),
            "files_written": state.get("written_files", []),
            "commit_output": state.get("commit_output"),
            "push_output": state.get("push_output"),
            "head_sha": state.get("head_sha"),
            "pr_auth_source": state.get("pr_auth_source") or "environment_or_context",
        }

    state["final_output"] = {
        "outputs": outputs,
        "notes": notes,
    }

    return state
