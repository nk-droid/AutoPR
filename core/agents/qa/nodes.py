from typing import Any

from core.contracts.code import CodeOutput
from core.contracts.enums import CheckStatus
from core.contracts.plan import PlanStep
from core.contracts.qa import QACheck, QAOutput
from core.contracts.run_context import ToolRunResult
from core.orchestrator.models import StageStatus
from infra.qa.aggregator import QAResultAggregator
from infra.qa.models import CoverageResult, LintResult, SecurityResult, TestResult

_REQUIRED_TOOLS = ("lint", "tests", "coverage", "security")

def _tail(text: str, max_chars: int = 1200) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]

def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""

def _tool_result_to_check(tool_name: str, result: ToolRunResult | None) -> QACheck:
    if result is None:
        return QACheck(
            name=f"{tool_name}_result",
            status=CheckStatus.FAIL,
            details="missing tool result",
        )
    payload = result.payload
    details_parts = [
        f"status={result.status.value}",
        f"reason={_payload_text(payload, 'reason')}",
        f"raw_output={_tail(_payload_text(payload, 'raw_output'))}",
        f"stdout={_tail(_payload_text(payload, 'stdout'))}",
        f"stderr={_tail(_payload_text(payload, 'stderr'))}",
    ]
    return QACheck(
        name=f"{tool_name}_result",
        status=result.status,
        details=" | ".join(details_parts),
    )

def _as_test_result(result: ToolRunResult | None) -> TestResult:
    if result is None:
        return TestResult(success=False)
    payload = dict(result.payload)
    payload["success"] = result.status == CheckStatus.PASS
    try:
        return TestResult.model_validate(payload)
    except Exception:
        return TestResult(success=False)

def _as_coverage_result(result: ToolRunResult | None) -> CoverageResult:
    if result is None:
        return CoverageResult(success=False, coverage_pct=0.0, threshold_passed=False)
    payload = dict(result.payload)
    payload["success"] = result.status == CheckStatus.PASS
    try:
        return CoverageResult.model_validate(payload)
    except Exception:
        return CoverageResult(success=False, coverage_pct=0.0, threshold_passed=False)

def _as_lint_result(result: ToolRunResult | None) -> LintResult:
    if result is None:
        return LintResult(success=False)
    payload = dict(result.payload)
    payload["success"] = result.status == CheckStatus.PASS
    try:
        return LintResult.model_validate(payload)
    except Exception:
        return LintResult(success=False)

def _as_security_result(result: ToolRunResult | None) -> SecurityResult:
    if result is None:
        return SecurityResult(success=False)
    payload = dict(result.payload)
    payload["success"] = result.status == CheckStatus.PASS
    try:
        return SecurityResult.model_validate(payload)
    except Exception:
        return SecurityResult(success=False)

def evaluate_inputs(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state.get("coding_output"), CodeOutput):
        state["status"] = StageStatus.BLOCKED
        state["summary"] = "QA blocked: coding_output must be CodeOutput."
        state["notes"] = {"blocking_reason": "invalid_coding_output"}
        return state
    if not isinstance(state.get("coding_step"), PlanStep):
        state["status"] = StageStatus.BLOCKED
        state["summary"] = "QA blocked: coding_step must be PlanStep."
        state["notes"] = {"blocking_reason": "invalid_coding_step"}
        return state
    tool_results = state.get("tool_results")
    if not isinstance(tool_results, list) or any(not isinstance(item, ToolRunResult) for item in tool_results):
        state["status"] = StageStatus.BLOCKED
        state["summary"] = "QA blocked: tool_results must be list[ToolRunResult]."
        state["notes"] = {"blocking_reason": "invalid_tool_results"}
        return state
    state["status"] = StageStatus.OK
    return state

def run_checks(state: dict[str, Any]) -> dict[str, Any]:
    coding_output = state.get("coding_output")
    coding_step = state.get("coding_step")
    tool_results = state.get("tool_results")
    if not isinstance(coding_output, CodeOutput) or not isinstance(coding_step, PlanStep) or not isinstance(tool_results, list):
        state["status"] = StageStatus.BLOCKED
        state["summary"] = "QA blocked: invalid typed inputs."
        state["notes"] = {"blocking_reason": "invalid_inputs"}
        return state
    if any(not isinstance(item, ToolRunResult) for item in tool_results):
        state["status"] = StageStatus.BLOCKED
        state["summary"] = "QA blocked: tool_results contains invalid entries."
        state["notes"] = {"blocking_reason": "invalid_tool_results"}
        return state

    combined_payload = dict(coding_output.files_map)
    combined_payload.update(coding_output.tests_map)
    files_changed = [path for path in combined_payload.keys() if path]
    planned_tests = [test.strip() for test in coding_step.tests if test.strip()]

    results_by_name = {item.name: item for item in tool_results}
    missing_tools = [name for name in _REQUIRED_TOOLS if name not in results_by_name]

    checks = [_tool_result_to_check(name, results_by_name.get(name)) for name in _REQUIRED_TOOLS]
    checks.append(
        QACheck(
            name="files_changed_present",
            status=CheckStatus.PASS if files_changed else CheckStatus.FAIL,
            details=f"generated_files_count={len(files_changed)}",
        )
    )
    checks.append(
        QACheck(
            name="tests_listed",
            status=CheckStatus.PASS if planned_tests else CheckStatus.WARN,
            details=f"planned_tests_count={len(planned_tests)}",
        )
    )

    qa_aggregate = QAResultAggregator().aggregate(
        test_result=_as_test_result(results_by_name.get("tests")),
        coverage_result=_as_coverage_result(results_by_name.get("coverage")),
        lint_result=_as_lint_result(results_by_name.get("lint")),
        security_result=_as_security_result(results_by_name.get("security")),
    )
    # Coverage shortfalls degrade to needs_review; missing/failing critical checks block.
    hard_block = (
        not files_changed
        or bool(missing_tools)
        or not qa_aggregate.tests_passed
        or not qa_aggregate.lint_passed
        or not qa_aggregate.security_passed
    )
    if hard_block:
        status = StageStatus.BLOCKED
    elif not qa_aggregate.coverage_passed:
        status = StageStatus.NEEDS_REVIEW
    else:
        status = StageStatus.OK
    state["status"] = status
    state["checks"] = checks
    state["summary"] = (
        f"QA checks complete: {sum(c.status == CheckStatus.PASS for c in checks)} pass, "
        f"{sum(c.status == CheckStatus.WARN for c in checks)} warn, "
        f"{sum(c.status == CheckStatus.FAIL for c in checks)} fail."
    )
    state["notes"] = {
        "generated_files_count": len(files_changed),
        "planned_tests_count": len(planned_tests),
        "tool_results_keys": sorted(results_by_name.keys()),
        "missing_tools": missing_tools,
        "aggregate_summary": qa_aggregate.summary,
        "tests_passed": qa_aggregate.tests_passed,
        "coverage_passed": qa_aggregate.coverage_passed,
        "lint_passed": qa_aggregate.lint_passed,
        "security_passed": qa_aggregate.security_passed,
    }
    return state

def finalize(state: dict[str, Any]) -> dict[str, Any]:
    raw_checks = state.get("checks", [])
    checks = raw_checks if isinstance(raw_checks, list) and all(isinstance(item, QACheck) for item in raw_checks) else []
    stage_status = state.get("status", StageStatus.BLOCKED)
    if not isinstance(stage_status, StageStatus):
        stage_status = StageStatus.BLOCKED
    summary = state.get("summary", "")
    notes = state.get("notes", {})
    result = QAOutput(
        status=stage_status,
        summary=summary.strip() if isinstance(summary, str) else "",
        checks=checks,
        notes=notes if isinstance(notes, dict) else {},
    )
    state["status"] = stage_status
    state["checks"] = checks
    state["final_output"] = result.model_dump(mode="json")
    return state
