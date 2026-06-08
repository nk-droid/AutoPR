import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from infra.qa.models import CommandResult
from infra.repo_worker.workspace import get_work_base, keep_qa_workspace

class Sandbox:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self.workspace: Path | None = None

    def __enter__(self) -> "Sandbox":
        root = Path(tempfile.mkdtemp(prefix="autopr_qa_", dir=str(get_work_base())))
        target = root / self.repo_path.name
        # Execute tools against an isolated copy to avoid mutating caller workspace.
        shutil.copytree(self.repo_path, target, dirs_exist_ok=True)
        self.workspace = target
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.workspace and self.workspace.exists():
            if keep_qa_workspace():
                print(f"[qa] retained sandbox: {self.workspace}")
            else:
                shutil.rmtree(self.workspace.parent, ignore_errors=True)
                self.workspace = None

    def run(self, command: list[str], timeout: int = 300) -> CommandResult:
        if self.workspace is None:
            raise RuntimeError("Sandbox workspace is not initialized")
        
        start = time.time()
        process = subprocess.run(
            command,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        end = time.time()
        
        return CommandResult(
            success=process.returncode == 0,
            exit_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            duration_sec=end - start,
        )

    @property
    def python(self) -> str:
        return sys.executable
