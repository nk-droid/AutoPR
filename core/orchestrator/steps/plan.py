from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import PlanWorkerInput
from core.contracts.triage import AmbiguityResult, Risk, TaskSpec, TriageResult
from core.orchestrator.models import RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime

from infra.ray.actors import PlanWorker

from observability.tracing import pipeline_step_attrs, traced


class PlanStep(PipelineStep):
    stage = PipelineStage.PLAN
    success_state = RunState.PLANNED.value

    @traced("pipeline.plan_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        try:
            triage_result = TriageResult(
                task_spec=TaskSpec(**context.get("task_spec", {})),
                risk=Risk(**context.get("risk", {})),
                ambiguity=AmbiguityResult(**context.get("ambiguity", {})),
                questions=context.get("questions", []),
            )
        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": f"Plan blocked: invalid triage output ({exc})."},
            )

        repo_map = context.get("repo_map", "")
        if not isinstance(repo_map, str):
            repo_map = ""

        return runtime.run_worker(
            self.stage,
            PlanWorker.remote(),
            PlanWorkerInput(triage_result=triage_result, repo_map=repo_map),
        )
