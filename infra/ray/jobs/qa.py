import shutil
import tempfile
from pathlib import Path
from typing import Callable

from core.contracts.enums import CheckStatus
from core.contracts.run_context import QAJobPayload, ToolRunResult

from infra.qa.coverage_runner import CoverageRunner
from infra.qa.lint_runner import LintRunner
from infra.qa.sandbox import Sandbox
from infra.qa.security_runner import SecurityRunner
from infra.qa.test_runner import TestRunner

def _collect_generated(qa_payload: QAJobPayload) -> dict[str, str]:
    return {
        **qa_payload.coding_output.files_map,
        **qa_payload.coding_output.tests_map,
    }

def _safe_rel(path: str) -> Path:
    p = Path(path.strip())

    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Invalid generated path: {path}")

    return p

def _materialize_workspace(
    qa_payload: QAJobPayload,
) -> tuple[Path, Callable[[], None]]:
    temp = tempfile.TemporaryDirectory(prefix="autopr-qa-")
    ws = Path(temp.name)

    repo_path = qa_payload.repo_path or ""

    if repo_path:
        src = Path(repo_path).expanduser().resolve()

        if src.exists() and src.is_dir():
            shutil.copytree(
                src,
                ws,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    "node_modules",
                ),
            )

    for rel_path, content in _collect_generated(qa_payload).items():
        dst = ws / _safe_rel(rel_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")

    return ws, temp.cleanup

def _collect_targets(
    qa_payload: QAJobPayload,
) -> tuple[list[str], list[str]]:
    generated = _collect_generated(qa_payload)

    py_targets = [
        path for path in generated
        if path.endswith(".py")
    ]

    test_targets = [
        path
        for path in qa_payload.coding_output.tests_map
        if path.strip()
    ]

    if not test_targets:
        test_targets = [
            target.split("::", 1)[0].strip()
            for target in qa_payload.coding_step.tests
            if target.strip()
        ]

    return py_targets, test_targets

def _is_probable_test_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/").lower()

    if not normalized:
        return False

    filename = normalized.rsplit("/", 1)[-1]

    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or filename.startswith("test_")
        or filename.endswith("_test.py")
    )

def _result(
    name: str,
    status: CheckStatus,
    payload: dict | None = None,
) -> ToolRunResult:
    return ToolRunResult(
        name=name,
        status=status,
        payload=payload or {},
    )

def _run_in_sandbox(
    qa_payload: QAJobPayload,
    callback: Callable[[Sandbox], ToolRunResult],
) -> ToolRunResult:
    ws, cleanup = _materialize_workspace(qa_payload)

    try:
        with Sandbox(str(ws)) as sandbox:
            return callback(sandbox)
    finally:
        cleanup()

def _execute_job(
    *,
    qa_payload: QAJobPayload,
    name: str,
    targets: list[str],
    empty_reason: str,
    runner_factory: Callable[[Sandbox], object],
    status_resolver: Callable[[object], CheckStatus],
) -> ToolRunResult:
    if not targets:
        return _result(
            name,
            CheckStatus.WARN,
            {"reason": empty_reason},
        )

    def _run(sandbox: Sandbox) -> ToolRunResult:
        runner = runner_factory(sandbox)

        result = runner.run(
            targets=targets,
            timeout=qa_payload.qa_timeout_sec,
        )

        return _result(
            name=name,
            status=status_resolver(result),
            payload=result.model_dump(mode="json"),
        )

    try:
        return _run_in_sandbox(qa_payload, _run)

    except Exception as exc:
        return _result(
            name,
            CheckStatus.FAIL,
            {"reason": str(exc)},
        )

def run_lint_job(qa_payload: QAJobPayload) -> ToolRunResult:
    py_targets, _ = _collect_targets(qa_payload)

    return _execute_job(
        qa_payload=qa_payload,
        name="lint",
        targets=py_targets,
        empty_reason="no_python_targets",
        runner_factory=lambda sb: LintRunner(sb),
        status_resolver=lambda r: (
            CheckStatus.PASS if r.success else CheckStatus.FAIL
        ),
    )

def run_tests_job(qa_payload: QAJobPayload) -> ToolRunResult:
    _, test_targets = _collect_targets(qa_payload)

    return _execute_job(
        qa_payload=qa_payload,
        name="tests",
        targets=test_targets,
        empty_reason="no_test_targets",
        runner_factory=lambda sb: TestRunner(sb),
        status_resolver=lambda r: (
            CheckStatus.PASS if r.success else CheckStatus.FAIL
        ),
    )

def run_coverage_job(qa_payload: QAJobPayload) -> ToolRunResult:
    _, test_targets = _collect_targets(qa_payload)

    return _execute_job(
        qa_payload=qa_payload,
        name="coverage",
        targets=test_targets,
        empty_reason="no_test_targets",
        runner_factory=lambda sb: CoverageRunner(
            sb,
            threshold=qa_payload.coverage_threshold,
        ),
        status_resolver=lambda r: (
            CheckStatus.PASS
            if r.success and r.threshold_passed
            else CheckStatus.FAIL
        ),
    )

def run_security_job(qa_payload: QAJobPayload) -> ToolRunResult:
    py_targets, _ = _collect_targets(qa_payload)
    security_targets = [
        path
        for path in py_targets
        if not _is_probable_test_path(path)
    ]

    if py_targets and not security_targets:
        return _result(
            name="security",
            status=CheckStatus.PASS,
            payload={
                "success": True,
                "issues": [],
                "raw_output": "",
                "reason": "no_non_test_python_targets",
            },
        )

    return _execute_job(
        qa_payload=qa_payload,
        name="security",
        targets=security_targets,
        empty_reason="no_python_targets",
        runner_factory=lambda sb: SecurityRunner(sb),
        status_resolver=lambda r: (
            CheckStatus.PASS if r.success else CheckStatus.FAIL
        ),
    )
