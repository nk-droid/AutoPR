from typing import Any
from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import PublishWorkerInput
from core.orchestrator.models import RunModel, StageResult
from core.orchestrator.steps.base import PipelineStep, StepRuntime
from infra.ray.actors import PublishWorker
from observability.tracing import traced, pipeline_step_attrs


class PublishStep(PipelineStep):
    stage = PipelineStage.PUBLISH
    success_state = RunState.PUBLISHED.value

    @traced("pipeline.publish_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        payload = dict(context)
        payload["repository"] = context.get("repository") or run.repository
        payload["issue_number"] = context.get("issue_number") or run.issue_number
        payload["execute_remote_actions"] = bool(context.get("execute_remote_actions", False))
        payload["metadata"] = (
            context.get("metadata")
            if isinstance(context.get("metadata"), dict)
            else dict(run.metadata)
        )
        payload["run_id"] = str(run.run_id)
        worker_result = runtime.run_worker(
            self.stage,
            PublishWorker.remote(),
            PublishWorkerInput(context=payload),
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
