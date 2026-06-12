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
    """
    Prepare and validate the inputs required to merge a pull request.

    Args:
        state: A dictionary containing the current state of the merge process, including the merge context.
        ```
        {
            "context": {
                "repository": "owner/repo",
                "pull_request_number": 123,
                "execute_remote_actions": True,
                "merge_method": "squash",
                // other context fields...
            },
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the resolved merge parameters and status set to OK when inputs are valid, or BLOCKED/NEEDS_REVIEW with notes when the merge cannot proceed.
    """

    context = state.get("context")
    if not isinstance(context, dict):
        state["status"] = StageStatus.BLOCKED
        state["notes"] = {"reason": "Merge blocked: invalid context payload."}
        return state

    merge_decision = context.get("_merge_decision")

    # Block the merge if the context contains a _merge_decision that disallows merging,
    # and include the reason and any blocking reasons in the notes for observability.
    if isinstance(merge_decision, dict) and not bool(merge_decision.get("allowed", True)):
        reason_value = merge_decision.get("reason")
        reason = (
            reason_value
            if isinstance(reason_value, str) and reason_value
            else "Merge decision blocked"
        )
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

    # Block the merge if either repository or pull request number is missing
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

    # If the context explicitly indicates that remote actions should not be executed,
    # block the merge and set status to NEEDS_REVIEW to indicate that human intervention
    # is required to proceed.
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

    # Normalize the merge method and commit title, and set the prepared parameters in the state
    # for use in the merge node. Default the merge method to "squash" if not provided.
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
    """
    Merge the pull request on GitHub using the prepared merge parameters.

    Args:
        state: A dictionary containing the current state of the merge process, including the prepared merge parameters from the prepare node.
        ```
        {
            "status": StageStatus.OK,
            "context": {"github_token": "...", ...},
            "repository": "owner/repo",
            "pull_request_number": 123,
            "merge_method": "squash",
            "commit_title": "...",
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the merge_result and a status of OK when the merge succeeds, or BLOCKED with an error when it fails.
    """

    if state.get("status") != StageStatus.OK:
        return state

    context = state.get("context")

    # Block the merge if the context is missing or invalid
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

    # Create a GitHub client using the provided token
    token_value = context.get("github_token")
    token = token_value if isinstance(token_value, str) else os.environ.get("GITHUB_TOKEN")
    client = GitHubClient(token=token)

    try:
        # Attempt to merge the pull request using the GitHub API client.
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

    # Add the merge method to the notes for observability
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
    """
    Finalize the merge process by compiling the merge result and notes into the final output.

    Args:
        state: A dictionary containing the current state of the merge process, including the merge result and notes.
        ```
        {
            "merge_result": {...},
            "notes": {...},
            // other state variables...
        }
        ```

    Returns:
        An updated state dictionary with the final output containing the merge outputs and notes.
    """

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
