import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from infra.repo_worker.git_utils import GitService

# Directories that never carry useful source context and bloat the repo map.
_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

def get_repos_base() -> Path:
    """Persistent base directory under which each repo is cloned to repos/<name>."""
    base = Path(os.environ.get("AUTOPR_REPOS_DIR", "repos")).expanduser()
    base = base if base.is_absolute() else (Path.cwd() / base)
    base.mkdir(parents=True, exist_ok=True)
    return base

def get_work_base() -> Path:
    """Scratch area for QA tool sandboxes, kept off /tmp and beside the clones."""
    work = get_repos_base() / ".work"
    work.mkdir(parents=True, exist_ok=True)
    return work

def keep_qa_workspace() -> bool:
    """When set, QA tool workspaces are retained under repos/.work for inspection."""
    return str(os.environ.get("AUTOPR_KEEP_QA_WORKSPACE", "")).strip().lower() in {"1", "true", "yes", "y", "on"}

def repo_dir_name(repository: str) -> str:
    """Map an "owner/name" slug to a single safe directory name."""
    name = repository.strip().strip("/").split("/")[-1] or "repo"
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)

def repo_workspace_path(repository: str) -> Path:
    return get_repos_base() / repo_dir_name(repository)

def _tokenized_https_url(clone_url: str, token: str) -> str:
    if not token:
        return clone_url
    parsed = urlsplit(clone_url)
    if parsed.scheme.lower() != "https" or not parsed.netloc or "@" in parsed.netloc:
        return clone_url
    safe_token = quote(token, safe="")
    netloc = f"x-access-token:{safe_token}@{parsed.netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

def _fresh_clone(repository: str, destination: Path, base_branch: str, token: str, clone_url: str | None) -> Path:
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    url = clone_url or f"https://github.com/{repository}.git"
    tokenized = _tokenized_https_url(url, token)
    candidates = [tokenized] + ([url] if url != tokenized else [])

    last_error: Exception | None = None
    for candidate_url in candidates:
        try:
            try:
                GitService.clone(candidate_url, destination, branch=base_branch)
            except Exception:
                # base_branch may not exist remotely yet; fall back to default branch.
                GitService.clone(candidate_url, destination)
            return destination
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Failed to clone {repository}: {last_error}")

def clone_repo(
    repository: str,
    base_branch: str,
    *,
    clone_url: str | None = None,
    token: str | None = None,
) -> Path:
    """Clone (or refresh) ``repository`` into a persistent repos/<name> directory.

    The directory is reused across runs: an existing checkout is hard-reset to a
    clean ``base_branch`` state; anything broken or non-git is re-cloned from
    scratch. The clone is never deleted by the pipeline.
    """
    resolved_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    destination = repo_workspace_path(repository)

    if (destination / ".git").is_dir():
        try:
            git = GitService(destination)
            git.fetch(remote="origin", prune=True)
            git._run(["checkout", base_branch])
            git._run(["reset", "--hard", f"origin/{base_branch}"])
            git._run(["clean", "-fd"])
            return destination
        except Exception:
            # Stale or corrupted checkout; rebuild it from scratch.
            pass

    return _fresh_clone(repository, destination, base_branch, resolved_token, clone_url)

def build_repo_map(repo_path: str | Path, *, max_files: int = 400) -> str:
    """Return a newline-separated, sorted listing of repo-relative file paths."""
    root = Path(repo_path)
    relative_paths: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in place so we never descend into them.
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for filename in filenames:
            relative_paths.append(os.path.relpath(os.path.join(dirpath, filename), root))

    relative_paths.sort()
    return "\n".join(relative_paths[:max_files])

def read_target_files(
    repo_path: str | Path,
    targets: list[str],
    *,
    max_bytes: int = 100_000,
) -> dict[str, str]:
    """Read the existing contents of ``targets`` (plan files/tests) from the clone.

    Paths are normalized (pytest node ids stripped), traversal outside the repo is
    rejected, and missing or oversized files are skipped. Files that don't exist yet
    are simply absent, so the code agent creates them from scratch.
    """
    root = Path(repo_path).resolve()
    contents: dict[str, str] = {}

    for target in targets:
        relative = target.split("::", 1)[0].strip()
        if not relative or relative in contents:
            continue

        try:
            resolved = (root / relative).resolve()
            resolved.relative_to(root)
        except Exception:
            continue

        if not resolved.is_file() or resolved.stat().st_size > max_bytes:
            continue

        try:
            contents[relative] = resolved.read_text(encoding="utf-8")
        except Exception:
            continue

    return contents
