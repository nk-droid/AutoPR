from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess, run
from typing import Sequence

class GitOperationError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, stderr: str) -> None:
        command_text = " ".join(command)
        message = f"Git command failed ({returncode}): {command_text}"
        if stderr:
            message = f"{message}\n{stderr.strip()}"
        super().__init__(message)
        self.command = list(command)
        self.returncode = returncode
        self.stderr = stderr

@dataclass
class GitService:
    repo_path: Path | str

    def __post_init__(self) -> None:
        self.repo_path = Path(self.repo_path).resolve()

    @classmethod
    def clone(
        cls,
        repo_url: str,
        destination: Path | str,
        *,
        branch: str | None = None,
    ) -> "GitService":
        destination_path = Path(destination).resolve()
        command = ["git", "clone"]
        if branch:
            command.extend(["--branch", branch])
        command.extend([repo_url, str(destination_path)])
        result: CompletedProcess[str] = run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitOperationError(command, result.returncode, result.stderr)
        return cls(destination_path)

    def _run(self, args: Sequence[str]) -> str:
        command = ["git", *args]
        result: CompletedProcess[str] = run(
            command,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitOperationError(command, result.returncode, result.stderr)
        return result.stdout.strip()

    def status(self, *, short: bool = False) -> str:
        args = ["status"]
        if short:
            args.append("--short")
        return self._run(args)

    def current_branch(self) -> str:
        return self._run(["rev-parse", "--abbrev-ref", "HEAD"])

    def head_sha(self) -> str:
        return self._run(["rev-parse", "HEAD"])

    def checkout_branch(self, branch: str, *, create: bool = False) -> str:
        if create:
            self._run(["checkout", "-b", branch])
        else:
            self._run(["checkout", branch])
        return branch

    def ensure_checkout_branch(
        self,
        branch: str,
        *,
        remote: str = "origin",
        base_branch: str = "main",
    ) -> str:
        try:
            return self.checkout_branch(branch, create=False)
        except GitOperationError:
            self.fetch(remote=remote, prune=False)
            try:
                self._run(["checkout", "-B", branch, f"{remote}/{branch}"])
                return branch
            except GitOperationError:
                try:
                    self.checkout_branch(base_branch, create=False)
                except GitOperationError:
                    pass
                return self.checkout_branch(branch, create=True)

    def pull(self, *, remote: str = "origin", branch: str | None = None, rebase: bool = False) -> str:
        args = ["pull"]
        if rebase:
            args.append("--rebase")
        args.append(remote)
        if branch:
            args.append(branch)
        return self._run(args)

    def push(
        self,
        *,
        remote: str = "origin",
        branch: str | None = None,
        set_upstream: bool = False,
    ) -> str:
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.append(remote)
        args.append(branch or self.current_branch())
        return self._run(args)

    def fetch(self, *, remote: str = "origin", prune: bool = True) -> str:
        args = ["fetch", remote]
        if prune:
            args.append("--prune")
        return self._run(args)

    def add(self, *paths: str) -> str:
        normalized = list(paths) if paths else ["."]
        return self._run(["add", *normalized])

    def set_config(self, key: str, value: str) -> str:
        return self._run(["config", key, value])

    def commit(self, message: str, *, all_files: bool = False) -> str:
        args = ["commit", "-m", message]
        if all_files:
            args.insert(1, "-a")
        return self._run(args)

    def delete_branch(
        self,
        branch: str,
        *,
        force: bool = False,
        remote: str | None = None,
        delete_remote: bool = False,
    ) -> str:
        local_flag = "-D" if force else "-d"
        output_parts = [self._run(["branch", local_flag, branch])]
        if delete_remote:
            if not remote:
                raise ValueError("remote is required when delete_remote=True")
            output_parts.append(self._run(["push", remote, "--delete", branch]))
        return "\n".join([part for part in output_parts if part])

def checkout_branch(branch: str) -> str:
    return GitService(Path.cwd()).checkout_branch(branch)
