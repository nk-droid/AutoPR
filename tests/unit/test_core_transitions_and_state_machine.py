import pytest

from core.contracts.enums import PipelineStage
from core.contracts.enums import RunState
from core.orchestrator.models import MergeDecision
from core.orchestrator.models import RunType
from core.orchestrator.models import StageResult
from core.orchestrator.models import StageStatus
from core.orchestrator.state_machine import InvalidStateTransitionError
from core.orchestrator.state_machine import StateMachine
from core.orchestrator.steps.registry import steps_for_run_type
from core.orchestrator.transitions import allowed_next_states
from core.orchestrator.transitions import can_merge_pr
from core.orchestrator.transitions import can_open_pr
from core.orchestrator.transitions import can_transition
from core.orchestrator.transitions import next_state


def test_steps_for_run_type_matches_current_pipeline_order() -> None:
    issue_to_pr_stages = [step.stage for step in steps_for_run_type(RunType.ISSUE_TO_PR)]
    assert issue_to_pr_stages == [
        PipelineStage.TRIAGE,
        PipelineStage.PREPARE,
        PipelineStage.PLAN,
        PipelineStage.CODE,
        PipelineStage.QA,
        PipelineStage.PUBLISH,
        PipelineStage.PR_OPEN,
        PipelineStage.REVIEW,
        PipelineStage.MERGE,
    ]

    pr_to_merge_stages = [step.stage for step in steps_for_run_type(RunType.PR_TO_MERGE)]
    assert pr_to_merge_stages == [
        PipelineStage.REVIEW,
        PipelineStage.MERGE,
    ]


def test_allowed_next_states_and_can_transition() -> None:
    next_states = allowed_next_states(RunState.RECEIVED.value, RunType.ISSUE_TO_PR)
    assert RunState.TRIAGED.value in next_states
    assert RunState.BLOCKED.value in next_states
    assert (
        can_transition(RunState.RECEIVED.value, RunState.TRIAGED.value, RunType.ISSUE_TO_PR) is True
    )
    assert (
        can_transition(RunState.RECEIVED.value, RunState.BLOCKED.value, RunType.ISSUE_TO_PR) is True
    )
    assert (
        can_transition(RunState.BLOCKED.value, RunState.TRIAGED.value, RunType.ISSUE_TO_PR) is False
    )
    assert (
        can_transition(RunState.RECEIVED.value, RunState.MERGED.value, RunType.ISSUE_TO_PR) is False
    )
    assert (
        can_transition(RunState.RECEIVED.value, RunState.RECEIVED.value, RunType.ISSUE_TO_PR)
        is True
    )
    assert (
        can_transition(RunState.QA_RUNNING.value, RunState.CODING.value, RunType.ISSUE_TO_PR)
        is True
    )
    assert (
        can_transition(RunState.PR_OPENED.value, RunState.REVIEW_PENDING.value, RunType.ISSUE_TO_PR)
        is True
    )
    assert (
        can_transition(RunState.PR_OPENED.value, RunState.READY_TO_MERGE.value, RunType.ISSUE_TO_PR)
        is True
    )
    assert (
        can_transition(RunState.RECEIVED.value, RunState.REVIEW_PENDING.value, RunType.PR_TO_MERGE)
        is True
    )


def test_next_state_respects_transition_rules() -> None:
    assert next_state(RunState.RECEIVED.value, "", RunType.ISSUE_TO_PR) == RunState.RECEIVED.value
    assert (
        next_state(RunState.RECEIVED.value, RunState.TRIAGED.value, RunType.ISSUE_TO_PR)
        == RunState.TRIAGED.value
    )
    assert (
        next_state(RunState.RECEIVED.value, RunState.BLOCKED.value, RunType.ISSUE_TO_PR)
        == RunState.BLOCKED.value
    )
    assert (
        next_state(RunState.RECEIVED.value, RunState.MERGED.value, RunType.ISSUE_TO_PR)
        == RunState.RECEIVED.value
    )


def test_can_open_pr_and_can_merge_pr() -> None:
    decision_missing = can_open_pr(None)
    assert decision_missing.allowed is False
    assert "qa_result_missing" in decision_missing.blocking_reasons
    blocked = can_open_pr(StageResult(stage="qa", status=StageStatus.BLOCKED))
    assert blocked.allowed is False
    ok = can_open_pr(StageResult(stage="qa", status=StageStatus.OK))
    assert ok.allowed is True
    merge_missing = can_merge_pr(None)
    assert merge_missing.allowed is False
    merge_blocked = can_merge_pr(StageResult(stage="review", status=StageStatus.BLOCKED))
    assert merge_blocked.allowed is False
    policy_denied = can_merge_pr(
        StageResult(stage="review", status=StageStatus.OK),
        MergeDecision(allowed=False, reason="policy", blocking_reasons=["deny"]),
    )
    assert policy_denied.allowed is False
    merge_ok = can_merge_pr(StageResult(stage="review", status=StageStatus.OK))
    assert merge_ok.allowed is True


def test_state_machine_records_history_and_validates_transitions() -> None:
    sm = StateMachine(initial_state=RunState.RECEIVED.value, run_type=RunType.ISSUE_TO_PR)
    state = sm.transition(RunState.TRIAGED.value, reason="triage")
    assert state == RunState.TRIAGED.value
    assert len(sm.history) == 1
    assert sm.history[0].from_state == RunState.RECEIVED.value
    assert sm.history[0].to_state == RunState.TRIAGED.value
    with pytest.raises(InvalidStateTransitionError):
        sm.transition(RunState.MERGED.value, reason="invalid")
    force_state = sm.transition(RunState.MERGED.value, reason="force", validate=False)
    assert force_state == RunState.MERGED.value
    snapshot = sm.snapshot()
    assert snapshot["state"] == RunState.MERGED.value
    assert snapshot["run_type"] == RunType.ISSUE_TO_PR.value
    assert len(snapshot["history"]) == 2
