from typing import Any

import ray

from core.contracts.enums import PipelineStage, RunState
from core.orchestrator.models import RunModel, RunType, StageResult, StageStatus
from core.orchestrator.state_machine import StateMachine
from core.orchestrator.steps.base import is_success_status
from core.orchestrator.steps.registry import steps_for_run_type
from infra.storage.artifacts import record_run_event, save_artifact, upsert_run

import dotenv
dotenv.load_dotenv()

class Coordinator:
    def __init__(self, run: RunModel | None = None) -> None:
        self.run = run or RunModel(state=RunState.RECEIVED.value)
        self.state_machine = StateMachine(
            initial_state=self.run.state,
            run_type=self.run.run_type,
        )
        self._persist_run(event_type="run_initialized", payload={"state": self.run.state})

    def _persist_run(self, *, event_type: str | None = None, payload: dict[str, Any] | None = None) -> None:
        run_id = str(self.run.run_id)
        run_type = self.run.run_type.value if hasattr(self.run.run_type, "value") else str(self.run.run_type)
        run_payload = self.run.model_dump(mode="json")
        upsert_run(
            run_id=run_id,
            state=self.run.state,
            run_type=run_type,
            payload=run_payload,
        )
        if event_type:
            record_run_event(run_id, event_type, payload or {})

    def run_once(self, *, decision: str | None = None, reason: str = "") -> str:
        if decision:
            self.transition_to(decision, reason=reason)
        return self.state_machine.state

    def transition_to(
        self,
        next_state: str,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        state = self.state_machine.transition(next_state, reason=reason, metadata=metadata)
        self.run.state = state
        self.run.transition_history = list(self.state_machine.history)
        self._persist_run(
            event_type="state_transition",
            payload={
                "state": state,
                "reason": reason,
                "metadata": metadata or {},
            },
        )
        return state

    def set_run_type(self, run_type: RunType) -> None:
        self.run.run_type = run_type
        self.state_machine.set_run_type(run_type)
        self._persist_run(
            event_type="run_type_set",
            payload={"run_type": run_type.value if hasattr(run_type, "value") else str(run_type)},
        )

    def add_stage_result(self, result: StageResult) -> StageResult:
        self.run.stage_results.append(result)
        run_id = str(self.run.run_id)
        stage_name = str(result.stage)
        stage_status = result.status.value if hasattr(result.status, "value") else str(result.status)
        artifact_key = f"stage_result:{len(self.run.stage_results) - 1}:{stage_name}"
        save_artifact(
            run_id,
            artifact_key,
            {
                "stage": stage_name,
                "status": stage_status,
                "outputs": result.outputs if isinstance(result.outputs, dict) else {},
                "notes": result.notes if isinstance(result.notes, dict) else {},
            },
        )
        self._persist_run(
            event_type="stage_result",
            payload={"stage": stage_name, "status": stage_status},
        )
        return result

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.isdigit():
                return int(candidate)
        return None

    def _seed_context_from_run(self, context: dict[str, Any]) -> None:
        if not isinstance(context.get("repository"), str) or not str(context.get("repository", "")).strip():
            if self.run.repository:
                context["repository"] = self.run.repository
        if context.get("issue_number") is None and self.run.issue_number is not None:
            context["issue_number"] = self.run.issue_number
        if context.get("pull_request_number") is None and self.run.pull_request_number is not None:
            context["pull_request_number"] = self.run.pull_request_number
        if "metadata" not in context and isinstance(self.run.metadata, dict):
            context["metadata"] = dict(self.run.metadata)

    def _sync_run_from_context(self, context: dict[str, Any]) -> None:
        repository = str(context.get("repository", "")).strip()
        if repository:
            self.run.repository = repository

        issue_number = self._coerce_int(context.get("issue_number"))
        if issue_number is not None:
            self.run.issue_number = issue_number

        pull_request_number = self._coerce_int(context.get("pull_request_number"))
        if pull_request_number is not None:
            self.run.pull_request_number = pull_request_number

        metadata = context.get("metadata")
        if isinstance(metadata, dict):
            self.run.metadata = dict(metadata)

    def run_worker(self, stage: PipelineStage, worker: Any, *args: Any) -> StageResult:
        worker_result_ref = worker.run.remote(*args)
        stage_status, worker_result = ray.get(worker_result_ref)
        outputs = worker_result if isinstance(worker_result, dict) else {"value": worker_result}
        return StageResult(
            stage=stage.value,
            status=stage_status,
            outputs=outputs,
        )

    def _run_steps(self, context: dict[str, Any]) -> RunModel:
        self._seed_context_from_run(context)
        self._sync_run_from_context(context)

        stage_results = context.get("_stage_results")
        if not isinstance(stage_results, dict):
            stage_results = {}
            context["_stage_results"] = stage_results

        for step in steps_for_run_type(self.run.run_type):
            for next_state, reason in step.before(context, self.run):
                self.transition_to(next_state, reason=reason)

            result = self.add_stage_result(step.execute(context, self.run, self))
            stage_results[str(result.stage)] = result

            if isinstance(result.outputs, dict):
                context.update(result.outputs)
            self._sync_run_from_context(context)

            for next_state, reason in step.after(result, context, self.run):
                self.transition_to(next_state, reason=reason or str(step.stage.value))

            if not is_success_status(result.status):
                break

        return self.run

    def run_issue_to_pr(self, context: dict[str, Any]) -> RunModel:
        self.set_run_type(RunType.ISSUE_TO_PR)
        return self._run_steps(context)

    def run_pr_to_merge(self, context: dict[str, Any]) -> RunModel:
        self.set_run_type(RunType.PR_TO_MERGE)
        return self._run_steps(context)
    
if __name__ == "__main__":
    import json
    import time
    import uuid

    started_at = time.time()
    run_model = RunModel(
        run_id=uuid.uuid4(),
        state=RunState.RECEIVED.value,
        run_type=RunType.ISSUE_TO_PR,
        repository="nk-droid/test",
        issue_number=1,
        pull_request_number=None,
        metadata={},
        stage_results=[],
        transition_history=[],
    )

    context = {
        "repository": run_model.repository,
        "issue_number": run_model.issue_number,
        "pull_request_number": run_model.pull_request_number,
        "metadata": run_model.metadata,
        "head_branch": "autopr/issue-1",   # required
        "base_branch": "main",             # optional (defaults to main)
        "execute_remote_actions": True,    # needed to actually open PR via API
    }
    coordinator = Coordinator(run_model)
    issue_to_pr_run = coordinator.run_issue_to_pr(context=context)

    raw_pr_number = context.get("pull_request_number")
    if isinstance(raw_pr_number, int):
        created_pr_number = raw_pr_number
    elif isinstance(raw_pr_number, str) and raw_pr_number.strip().isdigit():
        created_pr_number = int(raw_pr_number.strip())
    else:
        created_pr_number = issue_to_pr_run.pull_request_number

    final_run = issue_to_pr_run
    if issue_to_pr_run.state == RunState.PR_OPENED.value and created_pr_number is not None:
        context["pull_request_number"] = created_pr_number
        context["review_approved"] = True  # demo mode: allow merge workflow to proceed.
        final_run = coordinator.run_pr_to_merge(context=context)
    else:
        print("Skipping PR-to-merge workflow: PR was not opened in issue-to-PR run.")

    with open("result.json", "w", encoding="utf-8") as f:
        f.write(final_run.model_dump_json(indent=4))

    print(f"Final state: {final_run.state}")
    print(f"Total time: {time.time() - started_at}")
