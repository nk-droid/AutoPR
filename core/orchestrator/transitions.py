from core.contracts.enums import RunState
from core.orchestrator.models import MergeDecision, PRDecision, RunType, StageResult, StageStatus

# Issue-to-PR flow allows fallback from QA back to coding for iteration.
ISSUE_TO_PR_TRANSITIONS: dict[str, set[str]] = {
    RunState.RECEIVED.value: {RunState.TRIAGED.value, RunState.BLOCKED.value},
    RunState.TRIAGED.value: {RunState.PLANNED.value, RunState.BLOCKED.value},
    RunState.PLANNED.value: {RunState.CODING.value, RunState.BLOCKED.value},
    RunState.CODING.value: {RunState.QA_RUNNING.value, RunState.BLOCKED.value},
    RunState.QA_RUNNING.value: {
        RunState.PUBLISHED.value,
        RunState.CODING.value,
        RunState.BLOCKED.value,
    },
    RunState.PUBLISHED.value: {RunState.PR_OPENED.value, RunState.BLOCKED.value},
    RunState.PR_OPENED.value: {
        RunState.REVIEW_PENDING.value,
        RunState.READY_TO_MERGE.value,
        RunState.BLOCKED.value,
    },
    RunState.REVIEW_PENDING.value: {RunState.READY_TO_MERGE.value, RunState.BLOCKED.value},
    RunState.READY_TO_MERGE.value: {RunState.MERGED.value, RunState.BLOCKED.value},
    RunState.MERGED.value: set(),
    RunState.BLOCKED.value: set(),
}

# PR-to-merge flow starts from an already opened PR and skips earlier stages.
PR_TO_MERGE_TRANSITIONS: dict[str, set[str]] = {
    RunState.RECEIVED.value: {RunState.REVIEW_PENDING.value, RunState.BLOCKED.value},
    RunState.PR_OPENED.value: {RunState.REVIEW_PENDING.value, RunState.BLOCKED.value},
    RunState.REVIEW_PENDING.value: {RunState.READY_TO_MERGE.value, RunState.BLOCKED.value},
    RunState.READY_TO_MERGE.value: {RunState.MERGED.value, RunState.BLOCKED.value},
    RunState.MERGED.value: set(),
    RunState.BLOCKED.value: set(),
}

TRANSITIONS_BY_RUN_TYPE: dict[RunType, dict[str, set[str]]] = {
    RunType.ISSUE_TO_PR: ISSUE_TO_PR_TRANSITIONS,
    RunType.PR_TO_MERGE: PR_TO_MERGE_TRANSITIONS,
}

def allowed_next_states(current_state: str, run_type: RunType = RunType.ISSUE_TO_PR) -> set[str]:
    """
    Return all states reachable from the current state for a workflow.

    Args:
        current_state: State currently held by the run.
        run_type: Workflow graph used to evaluate allowed moves.

    Returns:
        Set of state values allowed as immediate next states.
    """

    transition_map = TRANSITIONS_BY_RUN_TYPE.get(run_type, ISSUE_TO_PR_TRANSITIONS)
    return transition_map.get(current_state, set())

def can_transition(
    current_state: str,
    candidate_state: str,
    run_type: RunType = RunType.ISSUE_TO_PR,
) -> bool:
    """
    Check whether a requested state change is valid for the workflow graph.

    Args:
        current_state: State currently held by the run.
        candidate_state: State requested by a caller or pipeline step.
        run_type: Workflow graph used to validate the transition.

    Returns:
        True when the transition is allowed or safely idempotent.
    """

    # No-op transitions are treated as valid to keep callers idempotent.
    if candidate_state == current_state:
        return True
    return candidate_state in allowed_next_states(current_state, run_type)

def next_state(
    current_state: str,
    decision: str,
    run_type: RunType = RunType.ISSUE_TO_PR,
) -> str:
    """
    Resolve a decision into the next accepted state for a workflow.

    Args:
        current_state: State currently held by the run.
        decision: Requested next state from the pipeline decision point.
        run_type: Workflow graph used to validate the decision.

    Returns:
        The requested state when valid, otherwise the current state.
    """

    if not decision:
        return current_state
    if can_transition(current_state, decision, run_type):
        return decision
    return current_state

def can_open_pr(qa_result: StageResult | None) -> PRDecision:
    """
    Decide whether QA output allows the pipeline to open a pull request.

    Args:
        qa_result: Latest QA stage result, when one exists.

    Returns:
        PR decision explaining whether PR creation may continue.
    """

    if qa_result is None:
        return PRDecision(
            allowed=False,
            reason="QA result missing",
            blocking_reasons=["qa_result_missing"],
        )
    if qa_result.status != StageStatus.OK:
        return PRDecision(
            allowed=False,
            reason="QA checks did not pass",
            blocking_reasons=["qa_not_green"],
        )
    return PRDecision(allowed=True, reason="QA checks passed")

def can_merge_pr(
    review_result: StageResult | None,
    policy_decision: MergeDecision | None = None,
) -> MergeDecision:
    """
    Decide whether review and policy results allow pull request merge.

    Args:
        review_result: Latest review stage result, when one exists.
        policy_decision: Optional policy gate result to honor before merge.

    Returns:
        Merge decision explaining whether merge may continue.
    """

    if review_result is None:
        return MergeDecision(
            allowed=False,
            reason="Review result missing",
            blocking_reasons=["review_result_missing"],
        )
    if review_result.status != StageStatus.OK:
        return MergeDecision(
            allowed=False,
            reason="Review is not in mergeable state",
            blocking_reasons=["review_not_green"],
        )
    if policy_decision and not policy_decision.allowed:
        return policy_decision
    return MergeDecision(allowed=True, reason="Review and policy checks passed")
