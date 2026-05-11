from pathlib import Path
from infra.repo_worker.git_utils import GitService

def execute_repo_job(repo_path: str, branch: str, *, remote: str = "origin") -> dict:
    git = GitService(Path(repo_path))
    current_branch = git.current_branch()
    if current_branch != branch:
        git.checkout_branch(branch)
    pull_output = git.pull(remote=remote, branch=branch)
    return {
        "repo_path": repo_path,
        "branch": branch,
        "remote": remote,
        "status": "ready",
        "pull_output": pull_output,
    }
