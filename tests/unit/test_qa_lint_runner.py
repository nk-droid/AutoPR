import json

from infra.qa.lint_runner import LintRunner
from infra.qa.models import CommandResult


class FakeSandbox:
    def __init__(self, result: CommandResult) -> None:
        self.python = "python3"
        self.result = result
        self.calls: list[tuple[list[str], int]] = []

    def run(self, command: list[str], timeout: int = 300) -> CommandResult:
        self.calls.append((command, timeout))
        return self.result


def test_lint_runner_returns_success_for_empty_output() -> None:
    sandbox = FakeSandbox(
        CommandResult(success=True, exit_code=0, stdout="   ", stderr="", duration_sec=0.1)
    )
    runner = LintRunner(sandbox)
    result = runner.run(timeout=15)
    assert result.success is True
    assert result.issues == []
    assert sandbox.calls[0][0] == [
        "python3",
        "-m",
        "ruff",
        "check",
        ".",
        "--output-format",
        "json",
    ]
    assert sandbox.calls[0][1] == 15


def test_lint_runner_parses_issues_from_json() -> None:
    payload = [
        {
            "filename": "pkg/a.py",
            "location": {"row": 7},
            "code": "F401",
            "message": "unused import",
        }
    ]
    sandbox = FakeSandbox(
        CommandResult(
            success=False,
            exit_code=1,
            stdout=json.dumps(payload),
            stderr="",
            duration_sec=0.2,
        )
    )
    runner = LintRunner(sandbox)
    result = runner.run(targets=["pkg"], timeout=20)
    assert result.success is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.file == "pkg/a.py"
    assert issue.line == 7
    assert issue.code == "F401"
    assert issue.message == "unused import"
    assert sandbox.calls[0][0] == [
        "python3",
        "-m",
        "ruff",
        "check",
        "pkg",
        "--output-format",
        "json",
    ]
