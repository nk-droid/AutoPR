from typing import Any

from core.contracts.enums import PipelineStage
from core.orchestrator.models import PRDecision
from core.orchestrator.models import RunModel
from core.orchestrator.models import StageResult
from core.orchestrator.models import StageStatus
from core.orchestrator.steps.code import CodeStep
from core.orchestrator.steps.merge import MergeStep
from core.orchestrator.steps.pr_open import PROpenStep
from core.orchestrator.steps.pr_open import _stage_results
from core.orchestrator.steps.publish import PublishStep
from core.orchestrator.steps.qa import QAStep
from core.orchestrator.steps.review import ReviewStep
from core.orchestrator.steps.triage import TriageStep

import core.orchestrator.steps.code as coding_steps
import core.orchestrator.steps.merge as merge_steps
import core.orchestrator.steps.pr_open as pr_open_steps
import core.orchestrator.steps.publish as publish_steps
import core.orchestrator.steps.triage as triage_steps


class _FakeRuntime:
    def __init__(self, result: StageResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def run_worker(self, stage: PipelineStage, worker: Any, *args: Any) -> StageResult:
        self.calls.append({"stage": stage, "worker": worker, "args": args})
        return self.result


def test_stage_results_helper_initializes_state_map() -> None:
    context: dict[str, Any] = {}
    result = _stage_results(context)
    assert result == {}
    assert context["_stage_results"] == {}


def test_triage_step_blocks_when_issue_title_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        triage_steps, "get_issue_details", lambda **kwargs: {"title": "", "body": ""}
    )
    step = TriageStep()
    run = RunModel(state="RECEIVED", repository="acme/repo", issue_number=5)
    result = step.execute(
        {"repository": "acme/repo", "issue_number": 5}, run, _FakeRuntime(StageResult(stage="x"))
    )
    assert result.status == StageStatus.BLOCKED
    assert "title is missing" in result.notes["reason"]


def test_code_step_normalizes_inputs_and_wraps_worker_output(monkeypatch) -> None:
    class _FakeRun:
        def remote(self, *args: Any, **kwargs: Any) -> object:
            return object()

    class _FakeActor:
        run = _FakeRun()

    class _FakeWorker:
        @staticmethod
        def remote() -> _FakeActor:
            return _FakeActor()

    worker_output = {
        "files_map": {"app/a.py": "print('x')"},
        "tests_map": {"tests/test_a.py": "def t(): pass"},
    }

    monkeypatch.setattr(coding_steps, "CodeWorker", _FakeWorker)
    monkeypatch.setattr(
        coding_steps.ray, "get", lambda refs: [(StageStatus.OK, worker_output) for _ in refs]
    )
    step = CodeStep()
    run = RunModel(state="PLANNED")
    runtime = _FakeRuntime(StageResult(stage=PipelineStage.CODE, status=StageStatus.OK))
    context = {
        "steps": [
            {
                "title": "Do work",
                "objective": "Patch code",
                "files": ["app/a.py"],
                "tests": ["tests/test_a.py::test_x"],
            }
        ],
        "repo_map": 123,
        "file_contents": {"app/a.py": "old", "ignore": 1},
    }
    result = step.execute(context, run, runtime)
    assert result.status == StageStatus.OK
    assert len(result.outputs["coding_order"]) == 1
    assert result.outputs["coding_step"]["files"] == ["app/a.py"]
    assert result.outputs["coding_step"]["tests"] == ["tests/test_a.py::test_x"]
    assert "files_map" in result.outputs["coding_output"]


def test_qa_step_blocks_when_coding_payload_is_invalid() -> None:
    step = QAStep()
    run = RunModel(state="CODING")
    result = step.execute(
        {"coding_output": {}, "coding_step": {}}, run, _FakeRuntime(StageResult(stage="x"))
    )
    assert result.status == StageStatus.BLOCKED
    assert "invalid coding payload" in result.notes["reason"]


def test_pr_open_step_respects_policy_block(monkeypatch) -> None:
    monkeypatch.setattr(
        pr_open_steps,
        "can_open_pr",
        lambda qa_result: PRDecision(
            allowed=False, reason="qa blocked", blocking_reasons=["qa_not_green"]
        ),
    )
    step = PROpenStep()
    run = RunModel(state="QA_RUNNING", issue_number=99, repository="acme/repo")
    context = {
        "_stage_results": {
            PipelineStage.QA.value: StageResult(
                stage=PipelineStage.QA.value, status=StageStatus.BLOCKED
            )
        }
    }
    result = step.execute(context, run, _FakeRuntime(StageResult(stage="x")))
    assert result.status == StageStatus.BLOCKED
    assert result.notes["reason"] == "qa blocked"
    assert result.notes["blocking_reasons"] == ["qa_not_green"]


def test_review_step_after_sets_blocked_when_policy_disallows() -> None:
    step = ReviewStep()
    run = RunModel(state="REVIEW_PENDING")
    result = StageResult(stage=PipelineStage.REVIEW.value, status=StageStatus.OK, notes={})
    transitions = step.after(
        result=result,
        context={
            "policy_decision": {
                "allowed": False,
                "reason": "manual block",
                "blocking_reasons": ["risk_high"],
            }
        },
        run=run,
    )
    assert transitions == []
    assert result.status == StageStatus.BLOCKED
    assert result.notes["reason"] == "manual block"
    assert result.notes["blocking_reasons"] == ["risk_high"]


def test_merge_step_dispatches_worker_and_maps_outputs(monkeypatch) -> None:
    class _FakeWorker:
        @staticmethod
        def remote():
            return "merge-worker"

    monkeypatch.setattr(merge_steps, "MergeWorker", _FakeWorker)
    step = MergeStep()
    run = RunModel(
        state="READY_TO_MERGE", repository="acme/repo", pull_request_number=12, metadata={"m": 1}
    )
    runtime = _FakeRuntime(
        StageResult(
            stage=PipelineStage.MERGE,
            status=StageStatus.BLOCKED,
            outputs={
                "outputs": {"merge_output": {"status": "blocked", "merged": False}},
                "notes": {"reason": "policy", "blocking_reasons": ["x"]},
            },
        )
    )
    result = step.execute(
        {"_merge_decision": {"allowed": False, "reason": "policy", "blocking_reasons": ["x"]}},
        run,
        runtime,
    )
    assert result.status == StageStatus.BLOCKED
    assert result.outputs["merge_output"]["status"] == "blocked"
    assert result.notes["reason"] == "policy"
    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["stage"] == PipelineStage.MERGE
    assert call["worker"] == "merge-worker"
    payload = call["args"][0]
    assert payload.context["repository"] == "acme/repo"
    assert payload.context["pull_request_number"] == 12
    assert payload.context["execute_remote_actions"] is False
    assert payload.context["_merge_decision"]["reason"] == "policy"


def test_publish_step_dispatches_worker_and_maps_outputs(monkeypatch) -> None:
    class _FakeWorker:
        @staticmethod
        def remote():
            return "publish-worker"

    monkeypatch.setattr(publish_steps, "PublishWorker", _FakeWorker)
    step = PublishStep()
    run = RunModel(
        state="QA_RUNNING", repository="acme/repo", issue_number=7, metadata={"origin": "test"}
    )
    runtime = _FakeRuntime(
        StageResult(
            stage=PipelineStage.PUBLISH,
            status=StageStatus.OK,
            outputs={
                "outputs": {
                    "publish_output": "Published changes to autopr/issue-7.",
                    "head_branch": "autopr/issue-7",
                },
                "notes": {"head_branch": "autopr/issue-7", "pr_auth_source": "git_credential"},
            },
        )
    )
    result = step.execute({"execute_remote_actions": True}, run, runtime)
    assert result.status == StageStatus.OK
    assert result.outputs["publish_output"] == "Published changes to autopr/issue-7."
    assert result.notes["pr_auth_source"] == "git_credential"
    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["stage"] == PipelineStage.PUBLISH
    assert call["worker"] == "publish-worker"
    payload = call["args"][0]
    assert payload.context["repository"] == "acme/repo"
    assert payload.context["issue_number"] == 7
    assert payload.context["execute_remote_actions"] is True
    assert payload.context["metadata"] == {"origin": "test"}
    assert "run_id" in payload.context
