import json
from pathlib import Path

from infra.qa.models import CoverageFile, CoverageResult
from infra.qa.sandbox import Sandbox

class CoverageRunner:
    def __init__(self, sandbox: Sandbox, threshold: float = 80.0):
        self.sandbox = sandbox
        self.threshold = threshold

    def run(self, targets: list[str] | None = None, timeout: int = 300) -> CoverageResult:
        cmd = [self.sandbox.python, "-m", "coverage", "run", "-m", "pytest"]
        if targets:
            cmd.extend(targets)
        json_generation_cmd = [self.sandbox.python, "-m", "coverage", "json"]

        test_run = self.sandbox.run(cmd, timeout=timeout)
        _ = self.sandbox.run(json_generation_cmd, timeout=timeout)

        if self.sandbox.workspace is None:
            return CoverageResult(success=False, coverage_pct=0.0, threshold_passed=False)
        
        report_path = Path(self.sandbox.workspace) / "coverage.json"
        if not report_path.exists():
            return CoverageResult(success=False, coverage_pct=0.0, threshold_passed=False, raw_output=test_run.stderr)
        
        data = json.loads(report_path.read_text())
        total = float(data["totals"]["percent_covered"])
        files: list[CoverageFile] = []
        for file_path, info in data["files"].items():
            files.append(
                CoverageFile(
                    path=file_path,
                    coverage_pct=float(info["summary"]["percent_covered"]),
                )
            )
            
        return CoverageResult(
            success=test_run.success,
            coverage_pct=total,
            threshold_passed=total >= self.threshold,
            files=files,
            raw_output=json.dumps(data, indent=2),
        )
