import json

from infra.qa.models import CommandResult
from infra.qa.security_runner import SecurityRunner

class FakeSandbox:
    def __init__(self, result: CommandResult) -> None:
        self.python = "python3"
        self.result = result
        self.calls: list[tuple[list[str], int]] = []

    def run(self, command: list[str], timeout: int = 300) -> CommandResult:
        self.calls.append((command, timeout))
        return self.result

def test_security_runner_handles_invalid_json_output() -> None:
    sandbox = FakeSandbox(
        CommandResult(success=False, exit_code=1, stdout="not-json", stderr="boom", duration_sec=0.1)
    )
    runner = SecurityRunner(sandbox)
    result = runner.run(targets=["pkg"], timeout=7)
    assert result.success is False
    assert result.issues == []
    assert result.raw_output == "not-json"
    assert sandbox.calls[0][0] == ["python3", "-m", "bandit", "-r", "pkg", "-f", "json"]
    assert sandbox.calls[0][1] == 7

def test_security_runner_reports_issues() -> None:
    payload = {
        "results": [
            {
                "issue_severity": "HIGH",
                "filename": "src/app.py",
                "line_number": 13,
                "issue_text": "use of assert",
            }
        ]
    }
    sandbox = FakeSandbox(
        CommandResult(success=True, exit_code=0, stdout=json.dumps(payload), stderr="", duration_sec=0.1)
    )
    runner = SecurityRunner(sandbox)
    result = runner.run()
    assert result.success is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "HIGH"
    assert issue.file == "src/app.py"
    assert issue.line == 13
    assert issue.issue == "use of assert"
    assert sandbox.calls[0][0] == ["python3", "-m", "bandit", "-r", ".", "-f", "json"]

def test_security_runner_passes_when_no_issues() -> None:
    sandbox = FakeSandbox(
        CommandResult(success=True, exit_code=0, stdout=json.dumps({"results": []}), stderr="", duration_sec=0.1)
    )
    runner = SecurityRunner(sandbox)
    result = runner.run(targets=["src"])
    assert result.success is True
    assert result.issues == []
