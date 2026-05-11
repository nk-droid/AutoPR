from typing import Any
from core.contracts.review import ReviewCheck, ReviewOutput

def _to_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None

def evaluate_review(state: dict[str, Any]) -> dict[str, Any]:
    context = state.get("context", {})
    if not isinstance(context, dict):
        state["status"] = "blocked"
        state["summary"] = "Review blocked: invalid context payload."
        state["required_actions"] = ["Provide valid review context."]
        state["notes"] = {"blocking_reason": "invalid_context"}
        return state

    qa_output = context.get("qa_output", {})
    qa_status = ""
    if isinstance(qa_output, dict):
        qa_status = str(qa_output.get("status") or qa_output.get("qa_status") or "").strip().lower()
    if not qa_status:
        qa_status = str(context.get("qa_status", "")).strip().lower()

    pr_number = context.get("pull_request_number")
    pr_url = str(context.get("pull_request_url", "")).strip()
    pr_state = str(context.get("pull_request_state", "")).strip().lower()
    pr_draft = bool(context.get("pull_request_draft", False))
    pr_mergeable = context.get("pull_request_mergeable")
    pr_mergeable_state = str(context.get("pull_request_mergeable_state", "")).strip().lower()
    review_approved = _to_optional_bool(context.get("review_approved")) is True

    qa_check_status = "warn"
    qa_check_details = "qa status unavailable"
    if qa_status == "ok":
        qa_check_status = "pass"
        qa_check_details = "qa_output.status=ok"
    elif qa_status in {"blocked", "failed", "fail", "error"}:
        qa_check_status = "fail"
        qa_check_details = f"qa_output.status={qa_status}"
    elif isinstance(pr_mergeable, bool):
        qa_check_status = "pass" if pr_mergeable else "fail"
        qa_check_details = f"derived_from_pull_request.mergeable={pr_mergeable}"
    elif pr_mergeable_state in {"clean", "has_hooks", "unstable"}:
        qa_check_status = "pass"
        qa_check_details = f"derived_from_pull_request.mergeable_state={pr_mergeable_state}"
    elif pr_mergeable_state in {"dirty", "blocked", "behind", "draft"}:
        qa_check_status = "fail"
        qa_check_details = f"derived_from_pull_request.mergeable_state={pr_mergeable_state}"
    elif pr_mergeable_state:
        qa_check_status = "warn"
        qa_check_details = f"pull_request.mergeable_state={pr_mergeable_state}"

    checks: list[ReviewCheck] = []
    checks.append(
        ReviewCheck(
            name="qa_or_mergeability_green",
            status=qa_check_status,
            details=qa_check_details,
        )
    )
    checks.append(
        ReviewCheck(
            name="pull_request_exists",
            status="pass" if isinstance(pr_number, int) and pr_number > 0 else "fail",
            details=f"pull_request_number={pr_number}",
        )
    )
    checks.append(
        ReviewCheck(
            name="pull_request_open",
            status="pass" if pr_state == "open" else "fail",
            details=f"pull_request_state={pr_state or 'missing'}",
        )
    )
    checks.append(
        ReviewCheck(
            name="pull_request_not_draft",
            status="pass" if not pr_draft else "fail",
            details=f"pull_request_draft={pr_draft}",
        )
    )
    checks.append(
        ReviewCheck(
            name="pull_request_url_present",
            status="pass" if pr_url else "warn",
            details="Pull request URL should be populated for reviewer context.",
        )
    )
    checks.append(
        ReviewCheck(
            name="manual_approval_recorded",
            status="pass" if review_approved else "warn",
            details="Set context.review_approved=true when human review is completed.",
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

    required_actions: list[str] = []
    if qa_check_status == "fail":
        required_actions.append(
            "Resolve failing checks or update the branch until the pull request becomes mergeable."
        )
    elif qa_check_status == "warn":
        required_actions.append(
            "Confirm CI and required checks have completed; mergeability status is still unknown."
        )
    if not (isinstance(pr_number, int) and pr_number > 0):
        required_actions.append("Open a pull request and provide pull_request_number.")
    if pr_state != "open":
        required_actions.append("Re-open the pull request before merge.")
    if pr_draft:
        required_actions.append("Mark the pull request ready for review (not draft).")
    if review_approved is not True:
        required_actions.append("Mark review_approved=true after human approval.")
    if not pr_url:
        required_actions.append("Provide pull_request_url for reviewer context.")

    state["status"] = status
    state["checks"] = [check.model_dump() for check in checks]
    state["required_actions"] = required_actions
    state["summary"] = (
        f"Review checks complete: {sum(c.status == 'pass' for c in checks)} pass, "
        f"{sum(c.status == 'warn' for c in checks)} warn, "
        f"{sum(c.status == 'fail' for c in checks)} fail."
    )
    state["notes"] = {
        "qa_status": qa_status,
        "pull_request_number": pr_number,
        "pull_request_state": pr_state,
        "pull_request_draft": pr_draft,
        "pull_request_url_present": bool(pr_url),
        "pull_request_mergeable": pr_mergeable if isinstance(pr_mergeable, bool) else None,
        "pull_request_mergeable_state": pr_mergeable_state,
        "review_approved": review_approved is True,
    }
    return state

def finalize(state: dict[str, Any]) -> dict[str, Any]:
    checks: list[ReviewCheck] = []
    for item in state.get("checks", []):
        if isinstance(item, ReviewCheck):
            checks.append(item)
            continue
        if isinstance(item, dict):
            checks.append(
                ReviewCheck(
                    name=str(item.get("name", "")).strip(),
                    status=str(item.get("status", "warn")).strip().lower(),
                    details=str(item.get("details", "")),
                )
            )
    result = ReviewOutput(
        summary=str(state.get("summary", "")).strip(),
        checks=checks,
        required_actions=[
            action for action in state.get("required_actions", []) if isinstance(action, str) and action.strip()
        ],
    )
    state["final_output"] = result.model_dump()
    return state
