import importlib
import sys
import types
from typing import Any

from core.contracts.enums import PipelineStage
from core.contracts.enums import RunState
from core.contracts.run_context import IssueToPRContext
from core.orchestrator.models import RunModel
from core.orchestrator.models import RunType
from core.orchestrator.models import StageResult
from core.orchestrator.models import StageStatus


def _load_coordinator_module():
    calls: dict[str, list[dict[str, Any]]] = {
        "upsert_run": [],
        "record_run_event": [],
        "save_artifact": [],
        "create_review_request": [],
        "attach_review_request_slack_ref": [],
        "send_needs_review_notification": [],
    }

    fake_artifacts = types.ModuleType("infra.storage.artifacts")

    def upsert_run(**kwargs):
        calls["upsert_run"].append(kwargs)

    def record_run_event(run_id: str, event_type: str, payload: dict[str, Any]):
        calls["record_run_event"].append(
            {"run_id": run_id, "event_type": event_type, "payload": payload}
        )

    def save_artifact(run_id: str, key: str, value: dict[str, Any]):
        calls["save_artifact"].append({"run_id": run_id, "key": key, "value": value})
        return {"saved": True}

    fake_artifacts.upsert_run = upsert_run
    fake_artifacts.record_run_event = record_run_event
    fake_artifacts.save_artifact = save_artifact

    fake_review_requests = types.ModuleType("infra.storage.review_requests")

    def create_review_request(**kwargs):
        calls["create_review_request"].append(kwargs)
        return {"request_id": "rq-1"}

    def attach_review_request_slack_ref(request_id: str, message_ref: str):
        calls["attach_review_request_slack_ref"].append(
            {"request_id": request_id, "message_ref": message_ref}
        )

    fake_review_requests.create_review_request = create_review_request
    fake_review_requests.attach_review_request_slack_ref = attach_review_request_slack_ref

    fake_slack = types.ModuleType("infra.slack.notification")

    def send_needs_review_notification(run, result, review_result):
        calls["send_needs_review_notification"].append(
            {
                "run_id": str(run.run_id),
                "stage": result.stage,
                "request_id": review_result.get("request_id"),
            }
        )
        return {"sent": True, "message_ref": "msg-1", "reason": "ok"}

    fake_slack.send_needs_review_notification = send_needs_review_notification

    sys.modules["infra.storage.artifacts"] = fake_artifacts
    sys.modules["infra.storage.review_requests"] = fake_review_requests
    sys.modules["infra.slack.notification"] = fake_slack
    sys.modules.pop("core.orchestrator.coordinator", None)
    module = importlib.import_module("core.orchestrator.coordinator")
    return module, calls


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type
        del exc
        del tb
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class _FakeTracer:
    def start_as_current_span(self, name: str, attributes: dict[str, Any]):
        del name
        del attributes
        return _FakeSpan()


class _Step:
    def __init__(
        self,
        *,
        stage: PipelineStage,
        result_factory,
        before: list[tuple[str, str]] | None = None,
        after: list[tuple[str, str]] | None = None,
        after_hook=None,
    ) -> None:
        self.stage = stage
        self._result_factory = result_factory
        self._before = before or []
        self._after = after or []
        self._after_hook = after_hook
        self.calls = 0

    def before(self, context: dict[str, Any], run: RunModel):
        del context
        del run
        return list(self._before)

    def execute(self, context: dict[str, Any], run: RunModel, runtime):
        del context
        del run
        del runtime
        self.calls += 1
        return self._result_factory(self.calls)

    def after(self, result: StageResult, context: dict[str, Any], run: RunModel):
        if self._after_hook is not None:
            return list(self._after_hook(result, context, run))
        del context
        del run
        return list(self._after)


def _issue_context() -> IssueToPRContext:
    return IssueToPRContext(
        repository="acme/repo",
        issue_number=10,
        execute_remote_actions=False,
        head_branch="autopr/issue-10",
        base_branch="main",
        metadata={"source": "test"},
    )


def test_coordinator_run_issue_to_pr_persists_transitions_and_artifacts(monkeypatch) -> None:
    coordinator_module, calls = _load_coordinator_module()
    monkeypatch.setattr(coordinator_module, "observe_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "observe_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "get_tracer", lambda: _FakeTracer())
    triage_step = _Step(
        stage=PipelineStage.TRIAGE,
        result_factory=lambda _count: StageResult(
            stage=PipelineStage.TRIAGE.value,
            status=StageStatus.OK,
            outputs={"task_spec": {"problem": "p"}},
        ),
        after=[(RunState.TRIAGED.value, "triaged")],
    )
    monkeypatch.setattr(coordinator_module, "steps_for_run_type", lambda _run_type: [triage_step])
    run = RunModel(state=RunState.RECEIVED.value, run_type=RunType.ISSUE_TO_PR)
    coordinator = coordinator_module.Coordinator(run)
    final_run = coordinator.run_issue_to_pr(_issue_context())
    assert final_run.state == RunState.TRIAGED.value
    assert triage_step.calls == 1
    assert any(item["event_type"] == "run_initialized" for item in calls["record_run_event"])
    assert any(item["event_type"] == "stage_result" for item in calls["record_run_event"])
    assert any(item["event_type"] == "state_transition" for item in calls["record_run_event"])
    assert calls["save_artifact"][0]["key"].startswith("stage_result:0:triage")
    assert len(calls["upsert_run"]) >= 3


def test_coordinator_qa_retry_loops_back_to_code(monkeypatch) -> None:
    coordinator_module, calls = _load_coordinator_module()
    monkeypatch.setattr(coordinator_module, "observe_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "observe_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "get_tracer", lambda: _FakeTracer())
    monkeypatch.setenv("QA_MAX_RETRIES", "1")
    code_step = _Step(
        stage=PipelineStage.CODE,
        result_factory=lambda _count: StageResult(
            stage=PipelineStage.CODE.value,
            status=StageStatus.OK,
            outputs={"coding_output": {"files_map": {"app.py": "x"}, "tests_map": {}}},
        ),
        after=[(RunState.CODING.value, "coding")],
    )

    def qa_result(call_count: int) -> StageResult:
        if call_count == 1:
            return StageResult(
                stage=PipelineStage.QA.value, status=StageStatus.BLOCKED, notes={"reason": "failed"}
            )
        return StageResult(
            stage=PipelineStage.QA.value,
            status=StageStatus.OK,
            outputs={"qa_output": {"status": "ok"}},
        )

    qa_step = _Step(
        stage=PipelineStage.QA,
        before=[(RunState.QA_RUNNING.value, "qa started")],
        result_factory=qa_result,
        after_hook=lambda result, _context, _run: (
            [(RunState.PUBLISHED.value, "qa done")] if result.status == StageStatus.OK else []
        ),
    )
    monkeypatch.setattr(
        coordinator_module, "steps_for_run_type", lambda _run_type: [code_step, qa_step]
    )
    run = RunModel(state=RunState.PLANNED.value, run_type=RunType.ISSUE_TO_PR)
    coordinator = coordinator_module.Coordinator(run)
    final_run = coordinator.run_issue_to_pr(_issue_context())
    assert code_step.calls == 2
    assert qa_step.calls == 2
    assert final_run.state == RunState.PUBLISHED.value
    assert any(item["event_type"] == "qa_retry_scheduled" for item in calls["record_run_event"])
    assert any(
        item["event_type"] == "state_transition" and item["payload"]["reason"] == "qa_retry_1"
        for item in calls["record_run_event"]
    )


def test_coordinator_publish_needs_review_creates_review_request(monkeypatch) -> None:
    coordinator_module, calls = _load_coordinator_module()
    monkeypatch.setattr(coordinator_module, "observe_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "observe_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "get_tracer", lambda: _FakeTracer())
    publish_step = _Step(
        stage=PipelineStage.PUBLISH,
        result_factory=lambda _count: StageResult(
            stage=PipelineStage.PUBLISH.value,
            status=StageStatus.NEEDS_REVIEW,
            notes={"reason": "manual approval required"},
        ),
    )
    monkeypatch.setattr(coordinator_module, "steps_for_run_type", lambda _run_type: [publish_step])
    run = RunModel(state=RunState.RECEIVED.value, run_type=RunType.ISSUE_TO_PR)
    coordinator = coordinator_module.Coordinator(run)
    final_run = coordinator.run_issue_to_pr(_issue_context())
    assert final_run.state == RunState.RECEIVED.value
    assert len(calls["create_review_request"]) == 1
    assert calls["create_review_request"][0]["stage"] == PipelineStage.PUBLISH.value
    assert calls["attach_review_request_slack_ref"][0]["request_id"] == "rq-1"
    assert calls["send_needs_review_notification"][0]["request_id"] == "rq-1"
    assert any(item["event_type"] == "needs_review_raised" for item in calls["record_run_event"])


def test_coordinator_blocks_on_max_autonomous_loops(monkeypatch) -> None:
    coordinator_module, calls = _load_coordinator_module()
    monkeypatch.setattr(coordinator_module, "observe_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "observe_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(coordinator_module, "get_tracer", lambda: _FakeTracer())
    monkeypatch.setenv("AUTOPR_MAX_AUTONOMOUS_LOOPS", "1")

    triage_step = _Step(
        stage=PipelineStage.TRIAGE,
        result_factory=lambda _count: StageResult(
            stage=PipelineStage.TRIAGE.value, status=StageStatus.OK
        ),
        after=[(RunState.TRIAGED.value, "triaged")],
    )
    plan_step = _Step(
        stage=PipelineStage.PLAN,
        result_factory=lambda _count: StageResult(
            stage=PipelineStage.PLAN.value, status=StageStatus.OK
        ),
        after=[(RunState.PLANNED.value, "planned")],
    )
    monkeypatch.setattr(
        coordinator_module, "steps_for_run_type", lambda _run_type: [triage_step, plan_step]
    )

    run = RunModel(state=RunState.RECEIVED.value, run_type=RunType.ISSUE_TO_PR)
    coordinator = coordinator_module.Coordinator(run)
    final_run = coordinator.run_issue_to_pr(_issue_context())

    assert triage_step.calls == 1  # first loop runs
    assert plan_step.calls == 0  # blocked before executing the 2nd step
    assert final_run.state == RunState.BLOCKED.value
    assert any(item["event_type"] == "run_blocked" for item in calls["record_run_event"])
