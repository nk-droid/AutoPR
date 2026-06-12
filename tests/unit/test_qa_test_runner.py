from infra.qa.models import CommandResult
from infra.qa import test_runner as qa_test_runner


class FakeSandbox:
    def __init__(self, result: CommandResult) -> None:
        self.python = "python3"
        self.result = result
        self.calls: list[tuple[list[str], int]] = []

    def run(self, command: list[str], timeout: int = 300) -> CommandResult:
        self.calls.append((command, timeout))
        return self.result


def test_test_runner_parses_passed_and_failed_counts() -> None:
    sandbox = FakeSandbox(
        CommandResult(
            success=False,
            exit_code=1,
            stdout="================ 2 passed, 1 failed in 0.22s ================",
            stderr="error details",
            duration_sec=0.22,
        )
    )
    runner = qa_test_runner.TestRunner(sandbox)
    result = runner.run(targets=["tests/unit"], timeout=12)
    assert result.success is False
    assert result.total == 3
    assert result.passed == 2
    assert result.failed == 1
    assert result.errors == ["error details"]
    assert sandbox.calls[0][0] == ["python3", "-m", "pytest", "-v", "tests/unit"]
    assert sandbox.calls[0][1] == 12


def test_test_runner_handles_success_with_no_summary_matches() -> None:
    sandbox = FakeSandbox(
        CommandResult(
            success=True,
            exit_code=0,
            stdout="all good",
            stderr="",
            duration_sec=0.1,
        )
    )
    runner = qa_test_runner.TestRunner(sandbox)
    result = runner.run()
    assert result.success is True
    assert result.total == 0
    assert result.passed == 0
    assert result.failed == 0
    assert result.errors == []
