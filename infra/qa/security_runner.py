import json

from infra.qa.models import SecurityIssue, SecurityResult
from infra.qa.sandbox import Sandbox

class SecurityRunner:
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox

    def run(self, targets: list[str] | None = None, timeout: int = 300) -> SecurityResult:
        cmd = [self.sandbox.python, "-m", "bandit", "-r"]
        cmd.extend(targets or ["."])
        cmd.extend(["-f", "json"])

        result = self.sandbox.run(cmd, timeout=timeout)

        try:
            data = json.loads(result.stdout)
        except Exception:
            # Bandit sometimes emits non-JSON output on runtime/tooling failures.
            return SecurityResult(success=False, issues=[], raw_output=result.stdout or result.stderr)
        
        issues: list[SecurityIssue] = []
        for item in data.get("results", []):
            issues.append(
                SecurityIssue(
                    severity=item["issue_severity"],
                    file=item["filename"],
                    line=item["line_number"],
                    issue=item["issue_text"],
                )
            )
            
        return SecurityResult(
            success=result.success and len(issues) == 0,
            issues=issues,
            raw_output=result.stdout,
        )
