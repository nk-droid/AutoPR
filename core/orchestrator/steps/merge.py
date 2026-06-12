from typing import Any
from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import MergeWorkerInput
from core.orchestrator.models import RunModel, StageResult
from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status
from infra.ray.actors import MergeWorker
from observability.tracing import traced, pipeline_step_attrs


class MergeStep(PipelineStep):
    stage = PipelineStage.MERGE

    @traced("pipeline.merge_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        payload = dict(context)
        payload["repository"] = context.get("repository") or run.repository
        if not isinstance(payload.get("pull_request_number"), int):
            payload["pull_request_number"] = run.pull_request_number
        payload["execute_remote_actions"] = bool(context.get("execute_remote_actions", False))
        payload["metadata"] = (
            context.get("metadata")
            if isinstance(context.get("metadata"), dict)
            else dict(run.metadata)
        )
        worker_result = runtime.run_worker(
            self.stage,
            MergeWorker.remote(),
            MergeWorkerInput(context=payload),
        )
        worker_payload = worker_result.outputs if isinstance(worker_result.outputs, dict) else {}
        outputs_value = worker_payload.get("outputs", {})
        notes_value = worker_payload.get("notes", {})
        outputs = outputs_value if isinstance(outputs_value, dict) else {}
        notes = notes_value if isinstance(notes_value, dict) else {}
        return StageResult(
            stage=self.stage,
            status=worker_result.status,
            outputs=outputs,
            notes=notes,
        )

    def after(
        self,
        result: StageResult,
        context: dict[str, Any],
        run: RunModel,
    ) -> list[tuple[str, str]]:
        if is_success_status(result.status):
            return [(RunState.MERGED.value, "merge completed")]
        return []
