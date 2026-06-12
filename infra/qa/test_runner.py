import re

from infra.qa.models import TestResult
from infra.qa.sandbox import Sandbox


class TestRunner:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    def run(self, targets: list[str] | None = None, timeout: int = 300) -> TestResult:
        cmd = [self.sandbox.python, "-m", "pytest", "-v"]
        if targets:
            cmd.extend(targets)

        result = self.sandbox.run(cmd, timeout=timeout)
        output = f"{result.stdout}\n{result.stderr}"

        # Parse pytest summary so downstream steps can reason over counts.
        passed = 0
        failed = 0
        passed_match = re.search(r"(\d+)\s+passed", output)
        failed_match = re.search(r"(\d+)\s+failed", output)
        if passed_match:
            passed = int(passed_match.group(1))
        if failed_match:
            failed = int(failed_match.group(1))

        return TestResult(
            success=result.success,
            total=passed + failed,
            passed=passed,
            failed=failed,
            errors=[] if result.success else [result.stderr],
            raw_output=output,
        )
