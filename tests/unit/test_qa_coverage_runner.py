import json

from infra.qa.coverage_runner import CoverageRunner
from infra.qa.models import CommandResult

class FakeSandbox:
    def __init__(self, responses: list[CommandResult], workspace: str | None) -> None:
        self.python = "python3"
        self.responses = list(responses)
        self.workspace = workspace
        self.calls: list[tuple[list[str], int]] = []

    def run(self, command: list[str], timeout: int = 300) -> CommandResult:
        self.calls.append((command, timeout))
        return self.responses.pop(0)

def test_coverage_runner_returns_failure_when_workspace_is_missing(tmp_path) -> None:
    sandbox = FakeSandbox(
        [
            CommandResult(success=True, exit_code=0, stdout="", stderr="", duration_sec=0.2),
            CommandResult(success=True, exit_code=0, stdout="", stderr="", duration_sec=0.1),
        ],
        workspace=None,
    )
    runner = CoverageRunner(sandbox)
    result = runner.run(targets=["tests"])
    assert result.success is False
    assert result.coverage_pct == 0.0
    assert result.threshold_passed is False
    assert sandbox.calls[0][0] == ["python3", "-m", "coverage", "run", "-m", "pytest", "tests"]
    assert sandbox.calls[1][0] == ["python3", "-m", "coverage", "json"]

def test_coverage_runner_returns_failure_when_report_file_is_missing(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = FakeSandbox(
        [
            CommandResult(success=False, exit_code=2, stdout="", stderr="failed tests", duration_sec=0.2),
            CommandResult(success=True, exit_code=0, stdout="", stderr="", duration_sec=0.1),
        ],
        workspace=str(workspace),
    )
    runner = CoverageRunner(sandbox)
    result = runner.run()
    assert result.success is False
    assert result.coverage_pct == 0.0
    assert result.threshold_passed is False
    assert result.raw_output == "failed tests"

def test_coverage_runner_parses_json_report(tmp_path) -> None:
    workspace = tmp_path / "ws2"
    workspace.mkdir()
    report = {
        "totals": {"percent_covered": 84.5},
        "files": {
            "src/a.py": {"summary": {"percent_covered": 80.0}},
            "src/b.py": {"summary": {"percent_covered": 90.0}},
        },
    }
    (workspace / "coverage.json").write_text(json.dumps(report), encoding="utf-8")
    sandbox = FakeSandbox(
        [
            CommandResult(success=True, exit_code=0, stdout="", stderr="", duration_sec=0.3),
            CommandResult(success=True, exit_code=0, stdout="", stderr="", duration_sec=0.1),
        ],
        workspace=str(workspace),
    )
    runner = CoverageRunner(sandbox, threshold=82.0)
    result = runner.run(timeout=11)
    assert result.success is True
    assert result.coverage_pct == 84.5
    assert result.threshold_passed is True
    assert {item.path: item.coverage_pct for item in result.files} == {
        "src/a.py": 80.0,
        "src/b.py": 90.0,
    }
    assert sandbox.calls[0][1] == 11
