import json

from infra.qa.models import LintIssue, LintResult
from infra.qa.sandbox import Sandbox

class LintRunner:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    def run(self, targets: list[str] | None = None, timeout: int = 300) -> LintResult:
        cmd = [self.sandbox.python, "-m", "ruff", "check"]
        cmd.extend(targets or ["."])
        cmd.extend(["--output-format", "json"])

        result = self.sandbox.run(cmd, timeout=timeout)
        output = result.stdout.strip()
        if not output:
            # Ruff returns empty JSON output when there are no findings.
            return LintResult(success=True, issues=[])
        
        parsed = json.loads(output)
        issues: list[LintIssue] = []
        for issue in parsed:
            issues.append(
                LintIssue(
                    file=issue["filename"],
                    line=issue["location"]["row"],
                    code=issue["code"],
                    message=issue["message"],
                )
            )
            
        return LintResult(
            success=len(issues) == 0,
            issues=issues,
            raw_output=output,
        )
