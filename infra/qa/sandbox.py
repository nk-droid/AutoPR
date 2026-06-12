import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from infra.qa.models import CommandResult
from infra.repo_worker.workspace import get_work_base, keep_qa_workspace

import logging

logger = logging.getLogger(__name__)


class Sandbox:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self.workspace: Path | None = None

    def __enter__(self) -> "Sandbox":
        try:
            root = Path(tempfile.mkdtemp(prefix="autopr_qa_", dir=str(get_work_base())))
            target = root / self.repo_path.name
            # Execute tools against an isolated copy to avoid mutating caller workspace.
            shutil.copytree(self.repo_path, target, dirs_exist_ok=True)
        except Exception as exc:
            logger.error(
                "qa sandbox setup failed",
                extra={
                    "event": "qa_sandbox_setup_failed",
                    "repo_path": str(self.repo_path),
                    "error": exc.__class__.__name__,
                },
            )
            raise
        self.workspace = target
        logger.debug(
            "qa sandbox created",
            extra={"event": "qa_sandbox_created", "workspace": str(target)},
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.workspace and self.workspace.exists():
            if keep_qa_workspace():
                logger.info(
                    "qa sandbox retained",
                    extra={"event": "qa_sandbox_retained", "workspace": str(self.workspace)},
                )
            else:
                shutil.rmtree(self.workspace.parent, ignore_errors=True)
                logger.debug(
                    "qa sandbox cleaned",
                    extra={"event": "qa_sandbox_cleaned", "workspace": str(self.workspace)},
                )
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
