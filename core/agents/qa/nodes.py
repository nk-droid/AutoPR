from typing import Any
from core.contracts.qa import QACheck, QAOutput

def _to_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item:
            continue
        result.append(item)
    return result

def evaluate_inputs(state: dict[str, Any]) -> dict[str, Any]:
    coding_output = state.get("coding_output", {})
    if not isinstance(coding_output, dict):
        state["status"] = "blocked"
        state["summary"] = "QA blocked: missing coding output payload."
        state["notes"] = {"blocking_reason": "missing_coding_output"}
        return state
    state["status"] = "ok"
    return state

def run_checks(state: dict[str, Any]) -> dict[str, Any]:
    coding_output = state.get("coding_output", {})
    coding_step = state.get("coding_step", {})
    files_map = coding_output.get("files_map", {})
    tests_map = coding_output.get("tests_map", {})
    if not isinstance(files_map, dict):
        files_map = {}
    if not isinstance(tests_map, dict):
        tests_map = {}
    legacy_files = coding_output.get("files", {})
    if not isinstance(legacy_files, dict):
        legacy_files = {}
    combined_payload = dict(files_map)
    combined_payload.update(tests_map)
    if not combined_payload:
        combined_payload = dict(legacy_files)
    files_changed = [path for path in combined_payload.keys() if isinstance(path, str) and path.strip()]
    coding_status = "ok" if files_changed else "blocked"
    planned_tests = _to_string_list(coding_step.get("tests", []))

    checks: list[QACheck] = []
    checks.append(
        QACheck(
            name="coding_status_green",
            status="pass" if coding_status == "ok" else "fail",
            details=f"derived_coding_status={coding_status or 'missing'}",
        )
    )
    checks.append(
        QACheck(
            name="files_changed_present",
            status="pass" if files_changed else "fail",
            details=f"generated_files_count={len(files_changed)}",
        )
    )
    checks.append(
        QACheck(
            name="tests_listed",
            status="pass" if planned_tests else "warn",
            details=f"planned_tests_count={len(planned_tests)}",
        )
    )

    has_failures = any(check.status == "fail" for check in checks)
    has_warnings = any(check.status == "warn" for check in checks)
    if has_failures:
        status = "blocked"
    elif has_warnings:
        status = "needs_review"
    else:
        status = "ok"

    state["status"] = status
    state["checks"] = [check.model_dump() for check in checks]
    state["summary"] = (
        f"QA checks complete: {sum(c.status == 'pass' for c in checks)} pass, "
        f"{sum(c.status == 'warn' for c in checks)} warn, "
        f"{sum(c.status == 'fail' for c in checks)} fail."
    )
    state["notes"] = {
        "coding_status": coding_status,
        "generated_files_count": len(files_changed),
        "planned_tests_count": len(planned_tests),
    }
    return state

def finalize(state: dict[str, Any]) -> dict[str, Any]:
    checks: list[QACheck] = []
    for item in state.get("checks", []):
        if isinstance(item, QACheck):
            checks.append(item)
            continue
        if isinstance(item, dict):
            checks.append(
                QACheck(
                    name=str(item.get("name", "")).strip(),
                    status=str(item.get("status", "warn")).strip().lower(),
                    details=str(item.get("details", "")),
                )
            )
    result = QAOutput(
        status=str(state.get("status", "blocked")).strip().lower(),
        summary=str(state.get("summary", "")).strip(),
        checks=checks,
        notes=dict(state.get("notes", {})),
    )
    state["final_output"] = result.model_dump()
    return state
