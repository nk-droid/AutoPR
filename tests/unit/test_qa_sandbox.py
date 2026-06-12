from pathlib import Path

import pytest

from infra.qa.sandbox import Sandbox


def test_sandbox_copies_repo_and_runs_command(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("hello", encoding="utf-8")
    with Sandbox(str(repo)) as sandbox:
        assert sandbox.workspace is not None
        copied_file = Path(sandbox.workspace) / "hello.txt"
        assert copied_file.exists()
        result = sandbox.run([sandbox.python, "-c", "print('qa-sandbox')"], timeout=5)
        assert result.success is True
        assert "qa-sandbox" in result.stdout
        assert result.exit_code == 0
        assert result.duration_sec >= 0
    assert sandbox.workspace is None


def test_sandbox_run_requires_context_manager(tmp_path) -> None:
    sandbox = Sandbox(str(tmp_path))
    with pytest.raises(RuntimeError, match="workspace is not initialized"):
        sandbox.run(["python3", "-c", "print('x')"])
