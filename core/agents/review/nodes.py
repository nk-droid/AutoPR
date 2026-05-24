from typing import Any

from core.contracts.enums import CheckStatus, GitHubMergeableState, GitHubPullRequestState
from core.contracts.review import ReviewCheck, ReviewOutput
from core.contracts.run_context import PRToMergeContext
from core.orchestrator.models import StageStatus

from observability.tracing import traced, langgraph_node_attrs

def _normalized_status(value: Any) -> str:
    if isinstance(value, StageStatus):
        return value.value
    if isinstance(value, CheckStatus):
        return value.value
    if isinstance(value, str):
        return value.strip().lower()
    return ""


@traced(
    "review_step.evaluate_review",
    attributes=langgraph_node_attrs("review", "evaluate_review"),
)
def evaluate_review(state: dict[str, Any]) -> dict[str, Any]:
    context = state.get("context")
    if not isinstance(context, PRToMergeContext):
        state["status"] = StageStatus.BLOCKED
        state["summary"] = "Review blocked: invalid context payload."
        state["required_actions"] = ["Provide valid review context."]
        state["notes"] = {"blocking_reason": "invalid_context"}
        return state

    payload = context.model_dump(mode="json")
    qa_output = payload.get("qa_output", {})
    qa_status = ""
    if isinstance(qa_output, dict):
        qa_status = _normalized_status(qa_output.get("status") or qa_output.get("qa_status"))
    if not qa_status:
        qa_status = _normalized_status(payload.get("qa_status"))

    qa_check_status = CheckStatus.WARN
    qa_check_details = "qa status unavailable"
    if qa_status == StageStatus.OK.value:
        qa_check_status = CheckStatus.PASS
        qa_check_details = "qa_output.status=ok"
    elif qa_status in {StageStatus.BLOCKED.value, StageStatus.FAILED.value, CheckStatus.FAIL.value, "failed", "error"}:
        qa_check_status = CheckStatus.FAIL
        qa_check_details = f"qa_output.status={qa_status}"
    elif isinstance(payload.get("pull_request_mergeable"), bool):
        # Fall back to live PR mergeability when qa_output is missing/incomplete.
        qa_check_status = CheckStatus.PASS if payload.get("pull_request_mergeable") else CheckStatus.FAIL
        qa_check_details = f"derived_from_pull_request.mergeable={payload.get('pull_request_mergeable')}"
    elif payload.get("pull_request_mergeable_state") in {
        GitHubMergeableState.CLEAN.value,
        GitHubMergeableState.HAS_HOOKS.value,
        GitHubMergeableState.UNSTABLE.value,
    }:
        qa_check_status = CheckStatus.PASS
        qa_check_details = f"derived_from_pull_request.mergeable_state={payload.get('pull_request_mergeable_state')}"
    elif payload.get("pull_request_mergeable_state") in {
        GitHubMergeableState.DIRTY.value,
        GitHubMergeableState.BLOCKED.value,
        GitHubMergeableState.BEHIND.value,
        GitHubMergeableState.DRAFT.value,
    }:
        qa_check_status = CheckStatus.FAIL
        qa_check_details = f"derived_from_pull_request.mergeable_state={payload.get('pull_request_mergeable_state')}"
    elif payload.get("pull_request_mergeable_state"):
        qa_check_status = CheckStatus.WARN
        qa_check_details = f"pull_request.mergeable_state={payload.get('pull_request_mergeable_state')}"

    checks = [
        ReviewCheck(name="qa_or_mergeability_green", status=qa_check_status, details=qa_check_details),
        ReviewCheck(
            name="pull_request_exists",
            status=CheckStatus.PASS if payload.get("pull_request_number", -1) > 0 else CheckStatus.FAIL,
            details=f"pull_request_number={payload.get('pull_request_number')}",
        ),
        ReviewCheck(
            name="pull_request_open",
            status=CheckStatus.PASS if payload.get("pull_request_state") == GitHubPullRequestState.OPEN.value else CheckStatus.FAIL,
            details=f"pull_request_state={payload.get('pull_request_state') or 'missing'}",
        ),
        ReviewCheck(
            name="pull_request_not_draft",
            status=CheckStatus.PASS if not payload.get("pull_request_draft", False) else CheckStatus.FAIL,
            details=f"pull_request_draft={payload.get('pull_request_draft', False)}",
        ),
        ReviewCheck(
            name="pull_request_url_present",
            status=CheckStatus.PASS if payload.get("pull_request_url") else CheckStatus.WARN,
            details="Pull request URL should be populated for reviewer context.",
        ),
        ReviewCheck(
            name="manual_approval_recorded",
            status=CheckStatus.PASS if payload.get("review_approved") else CheckStatus.WARN,
            details="Set context.review_approved=true when human review is completed.",
        ),
    ]

    has_failures = any(check.status == CheckStatus.FAIL for check in checks)
    has_warnings = any(check.status == CheckStatus.WARN for check in checks)
    if has_failures:
        status = StageStatus.BLOCKED
    elif has_warnings:
        status = StageStatus.NEEDS_REVIEW
    else:
        status = StageStatus.OK

    required_actions: list[str] = []
    if qa_check_status == CheckStatus.FAIL:
        required_actions.append(
            "Resolve failing checks or update the branch until the pull request becomes mergeable."
        )
    elif qa_check_status == CheckStatus.WARN:
        required_actions.append(
            "Confirm CI and required checks have completed; mergeability status is still unknown."
        )
    if not payload.get("pull_request_number", -1) > 0:
        required_actions.append("Open a pull request and provide pull_request_number.")
    if payload.get("pull_request_state") != GitHubPullRequestState.OPEN.value:
        required_actions.append("Re-open the pull request before merge.")
    if payload.get("pull_request_draft", False):
        required_actions.append("Mark the pull request ready for review (not draft).")
    if not payload.get("review_approved"):
        required_actions.append("Mark review_approved=true after human approval.")
    if not payload.get("pull_request_url"):
        required_actions.append("Provide pull_request_url for reviewer context.")

    state["status"] = status
    state["checks"] = checks
    state["required_actions"] = required_actions
    state["summary"] = (
        f"Review checks complete: {sum(c.status == CheckStatus.PASS for c in checks)} pass, "
        f"{sum(c.status == CheckStatus.WARN for c in checks)} warn, "
        f"{sum(c.status == CheckStatus.FAIL for c in checks)} fail."
    )
    state["notes"] = {
        "qa_status": qa_status,
        "pull_request_number": payload.get("pull_request_number", -1),
        "pull_request_state": payload.get("pull_request_state", ""),
        "pull_request_draft": payload.get("pull_request_draft", False),
        "pull_request_url_present": bool(payload.get("pull_request_url", "")),
        "pull_request_mergeable": payload.get("pull_request_mergeable", None),
        "pull_request_mergeable_state": payload.get("pull_request_mergeable_state", ""),
        "review_approved": payload.get("review_approved", False) is True,
    }
    return state

@traced(
    "review_step.finalize",
    attributes=langgraph_node_attrs("review", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    raw_checks = state.get("checks", [])
    checks = [item for item in raw_checks if isinstance(item, ReviewCheck)] if isinstance(raw_checks, list) else []
    required_actions = state.get("required_actions", [])
    result = ReviewOutput(
        summary=state.get("summary", ""),
        checks=checks,
        required_actions=required_actions if isinstance(required_actions, list) else [],
    )
    state["final_output"] = result.model_dump(mode="json")
    return state
