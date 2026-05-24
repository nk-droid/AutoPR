import os
from typing import Any
from core.orchestrator.models import StageStatus
from infra.github.client import GitHubClient
from observability.tracing import traced, langgraph_node_attrs

def _normalize_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""

@traced(
    "merge_step.prepare",
    attributes=langgraph_node_attrs("merge", "prepare"),
)
def prepare(state: dict[str, Any]) -> dict[str, Any]:
    context = state.get("context")
    if not isinstance(context, dict):
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Merge blocked: invalid context payload."}
        return state
    merge_decision = context.get("_merge_decision")
    if isinstance(merge_decision, dict) and not bool(merge_decision.get("allowed", True)):
        reason_value = merge_decision.get("reason")
        reason = reason_value if isinstance(reason_value, str) and reason_value else "Merge decision blocked"
        blocking_reasons = merge_decision.get("blocking_reasons", [])
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {
            "reason": reason,
            "blocking_reasons": blocking_reasons if isinstance(blocking_reasons, list) else [],
        }
        state["merge_result"] = {}
        return state
    repository = _normalize_text(context.get("repository"))
    raw_pr_number = context.get("pull_request_number")
    pull_request_number = raw_pr_number if isinstance(raw_pr_number, int) else None
    if not repository or pull_request_number is None:
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"blocking_reason": "missing_merge_inputs"}
        state["merge_result"] = {
            "status": StageStatus.BLOCKED.value,
            "merged": False,
            "message": "Merge blocked: repository or pull_request_number missing.",
            "merge_sha": "",
        }
        return state
    if not bool(context.get("execute_remote_actions", False)):
        state["status"] = StageStatus.NEEDS_REVIEW
        state["notes"] = {"blocking_reason": "remote_execution_disabled"}
        state["merge_result"] = {
            "status": StageStatus.NEEDS_REVIEW.value,
            "merged": False,
            "message": "Merge skipped: execute_remote_actions is False.",
            "merge_sha": "",
        }
        return state
    merge_method = _normalize_text(context.get("merge_method")) or "squash"
    commit_title_raw = context.get("merge_commit_title")
    commit_title = _normalize_text(commit_title_raw) or None
    state["status"] = StageStatus.OK
    state["repository"] = repository
    state["pull_request_number"] = pull_request_number
    state["merge_method"] = merge_method
    state["commit_title"] = commit_title
    state["notes"] = {"merge_method": merge_method}
    return state

@traced(
    "merge_step.merge",
    attributes=langgraph_node_attrs("merge", "merge"),
)
def merge(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("status") != StageStatus.OK:
        return state
    context = state.get("context")
    if not isinstance(context, dict):
        state["status"] = StageStatus.BLOCKED
        state["merge_result"] = {
            "status": StageStatus.BLOCKED.value,
            "merged": False,
            "message": "Merge failed.",
            "merge_sha": "",
            "error": "invalid_context",
        }
        return state
    token_value = context.get("github_token")
    token = token_value if isinstance(token_value, str) else os.environ.get("GITHUB_TOKEN")
    client = GitHubClient(token=token)
    try:
        payload = client.merge_pull_request(
            repo=_normalize_text(state.get("repository")),
            pull_number=state.get("pull_request_number"),
            merge_method=_normalize_text(state.get("merge_method")) or "squash",
            commit_title=state.get("commit_title"),
        )
        merged = bool(payload.get("merged"))
        message_value = payload.get("message")
        sha_value = payload.get("sha")
        state["merge_result"] = {
            "status": StageStatus.OK.value if merged else StageStatus.BLOCKED.value,
            "merged": merged,
            "message": _normalize_text(message_value),
            "merge_sha": _normalize_text(sha_value),
        }
        state["status"] = StageStatus.OK if merged else StageStatus.BLOCKED
    except Exception as exc:
        state["merge_result"] = {
            "status": StageStatus.BLOCKED.value,
            "merged": False,
            "message": "Merge failed.",
            "merge_sha": "",
            "error": str(exc),
        }
        state["status"] = StageStatus.BLOCKED
    finally:
        client.close()
    notes = state.get("notes", {})
    if not isinstance(notes, dict):
        notes = {}
    notes["merge_method"] = _normalize_text(state.get("merge_method")) or "squash"
    state["notes"] = notes
    return state

@traced(
    "merge_step.finalize",
    attributes=langgraph_node_attrs("merge", "finalize"),
)
def finalize(state: dict[str, Any]) -> dict[str, Any]:
    notes = state.get("notes", {})
    if not isinstance(notes, dict):
        notes = {}
    merge_result = state.get("merge_result")
    outputs: dict[str, Any] = {}
    if isinstance(merge_result, dict) and merge_result:
        outputs = {"merge_output": merge_result}
    state["final_output"] = {
        "outputs": outputs,
        "notes": notes,
    }
    return state
