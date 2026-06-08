import os
from pathlib import Path
from typing import Any

from core.contracts.enums import CheckStatus, GitHubMergeableState, GitHubPullRequestState
from core.contracts.review import LLMBlockingFinding, LLMMergeRiskReview, ReviewCheck, ReviewOutput
from core.contracts.run_context import PRToMergeContext
from core.orchestrator.models import StageStatus

from infra.github.client import GitHubClient
from infra.llm.chains import invoke_chain
from infra.llm.client import create_client
from infra.llm.prompts import load_prompt_catalog, require_prompt

from observability.tracing import traced, langgraph_node_attrs

_UNKNOWN_MERGEABLE_STATE = "unknown"
_PROMPTS_PATH = Path(__file__).with_name("prompts.yaml")
_PROMPTS = load_prompt_catalog(_PROMPTS_PATH)
LLM_MERGE_RISK_REVIEW_PROMPT = require_prompt(_PROMPTS, "llm_merge_risk_review", source=_PROMPTS_PATH)
llm_client = create_client()

class MergeabilityUnknownError(Exception):
    """Raised when a pull request's mergeability has not been computed yet.

    Signals the review graph's retry policy to wait and re-evaluate, giving
    GitHub time to resolve ``mergeable_state`` from ``unknown``.
    """

def _normalized_status(value: Any) -> str:
    if isinstance(value, StageStatus):
        return value.value
    if isinstance(value, CheckStatus):
        return value.value
    if isinstance(value, str):
        return value.strip().lower()
    return ""

def _fetch_live_mergeability(payload: dict[str, Any]) -> tuple[Any, Any] | None:
    """Re-fetch the PR from GitHub and return ``(mergeable, mergeable_state)``.

    Returns None when the lookup can't be performed or fails, so callers fall
    back to whatever mergeability was already in the payload.
    """
    repository = payload.get("repository")
    pull_request_number = payload.get("pull_request_number")
    if not repository or not isinstance(pull_request_number, int) or pull_request_number <= 0:
        return None

    token = payload.get("github_token") or os.environ.get("GITHUB_TOKEN")
    client = GitHubClient(token=token)
    try:
        pull_request = client.get_pull_request(repository, pull_request_number)
    except Exception:
        return None
    finally:
        client.close()

    return pull_request.get("mergeable"), pull_request.get("mergeable_state")


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

    if _normalized_status(payload.get("pull_request_mergeable_state")) in {"", _UNKNOWN_MERGEABLE_STATE}:
        refreshed = _fetch_live_mergeability(payload)
        if refreshed is not None:
            mergeable, mergeable_state = refreshed
            if mergeable is None or isinstance(mergeable, bool):
                payload["pull_request_mergeable"] = mergeable
            if mergeable_state is not None:
                payload["pull_request_mergeable_state"] = mergeable_state

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

    allow_unknown = bool(state.get("allow_unknown", False))
    if (
        not allow_unknown
        and qa_check_status == CheckStatus.WARN
        and _normalized_status(payload.get("pull_request_mergeable_state")) == _UNKNOWN_MERGEABLE_STATE
    ):
        raise MergeabilityUnknownError(
            f"pull request #{payload.get('pull_request_number')} mergeability is still unknown"
        )

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

def _review_payload(context: PRToMergeContext) -> dict[str, Any]:
    return context.model_dump(mode="json")

def _pipeline_context(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "risk": payload.get("risk", {}),
        "steps": payload.get("steps", []),
        "qa_output": payload.get("qa_output", {}),
        "policy_public_findings": payload.get("policy_public_findings", []),
        "review_approved": payload.get("review_approved", False),
    }

@traced(
    "review_step.llm_merge_risk_review",
    attributes=langgraph_node_attrs("review", "llm_merge_risk_review"),
)
def llm_merge_risk_review(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("status") != StageStatus.OK:
        return state

    context = state.get("context")
    if not isinstance(context, PRToMergeContext):
        return state

    payload = _review_payload(context)
    if bool(payload.get("llm_soft_gate_approved", False)):
        return state

    try:
        review = invoke_chain(
            template=LLM_MERGE_RISK_REVIEW_PROMPT.template,
            input_vars=LLM_MERGE_RISK_REVIEW_PROMPT.input_vars,
            output_model=LLMMergeRiskReview,
            variables={
                "pr_context": {
                    "repository": payload.get("repository", ""),
                    "pull_request_number": payload.get("pull_request_number", ""),
                    "pull_request_state": payload.get("pull_request_state", ""),
                    "pull_request_draft": payload.get("pull_request_draft", False),
                    "pull_request_mergeable_state": payload.get("pull_request_mergeable_state", ""),
                },
                "changed_files": payload.get("changed_files", []),
                "pipeline_context": _pipeline_context(payload),
            },
            agent="review_agent",
            node="llm_merge_risk_review",
            client=llm_client,
            include_format_instructions=LLM_MERGE_RISK_REVIEW_PROMPT.include_format_instructions,
        )
    except Exception:
        review = LLMMergeRiskReview(
            merge_risk="medium",
            confidence="low",
            summary="Manual review is recommended because automated merge-risk review could not complete.",
            blocking_findings=[
                LLMBlockingFinding(
                    severity="medium",
                    category="review_unavailable",
                    summary="Automated merge-risk review could not complete.",
                    suggested_fix="Have a reviewer inspect the pull request before merging.",
                )
            ],
        )

    state["llm_review"] = review
    return state

@traced(
    "review_step.finalize",
    attributes=langgraph_node_attrs("review", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    raw_checks = state.get("checks", [])
    checks = [item for item in raw_checks if isinstance(item, ReviewCheck)] if isinstance(raw_checks, list) else []
    required_actions = state.get("required_actions", [])
    llm_review_value = state.get("llm_review")
    llm_review: LLMMergeRiskReview | None = None
    if isinstance(llm_review_value, LLMMergeRiskReview):
        llm_review = llm_review_value
    elif isinstance(llm_review_value, dict):
        try:
            llm_review = LLMMergeRiskReview.model_validate(llm_review_value)
        except Exception:
            llm_review = None
    result = ReviewOutput(
        summary=state.get("summary", ""),
        checks=checks,
        required_actions=required_actions if isinstance(required_actions, list) else [],
        llm_review=llm_review,
    )
    state["final_output"] = result.model_dump(mode="json")
    print(f"Final review output: {state['final_output']}")
    return state
