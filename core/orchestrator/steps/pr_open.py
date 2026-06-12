from typing import Any, Dict

from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import IssueToPRContext, PRWorkerInput
from core.orchestrator.models import RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime
from core.orchestrator.transitions import can_open_pr

from infra.ray.actors import PRWorker

from observability.tracing import pipeline_step_attrs, traced


def _stage_results(context: Dict[str, Any]) -> Dict[str, StageResult]:
    # Keep prior stage outputs in context so later steps can make policy decisions.
    value = context.get("_stage_results")
    if isinstance(value, dict):
        return value
    value = {}
    context["_stage_results"] = value
    return value


class PROpenStep(PipelineStep):
    stage = PipelineStage.PR_OPEN
    success_state = RunState.PR_OPENED.value

    @traced("pipeline.pr_open_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        results = _stage_results(context)
        qa_result = results.get(PipelineStage.QA.value) or results.get(PipelineStage.QA)
        # PR creation is hard-gated on QA policy output.
        decision = can_open_pr(qa_result)
        if not decision.allowed:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": decision.reason, "blocking_reasons": decision.blocking_reasons},
            )
        issue_number = context.get("issue_number")
        if not isinstance(issue_number, int):
            issue_number = run.issue_number
        if not isinstance(issue_number, int):
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "PR open blocked: issue_number missing."},
            )
        payload = dict(context)
        payload.setdefault("repository", context.get("repository") or run.repository)
        payload.setdefault("issue_number", issue_number)
        payload.setdefault(
            "execute_remote_actions", bool(context.get("execute_remote_actions", False))
        )
        head_branch_value = context.get("head_branch") or context.get("pr_head")
        if not isinstance(head_branch_value, str) or not head_branch_value:
            head_branch_value = f"autopr/issue-{issue_number}"
        payload.setdefault("head_branch", head_branch_value)
        base_branch_value = context.get("base_branch") or context.get("pr_base")
        if not isinstance(base_branch_value, str) or not base_branch_value:
            base_branch_value = "main"
        payload.setdefault("base_branch", base_branch_value)
        payload.setdefault(
            "metadata",
            context.get("metadata")
            if isinstance(context.get("metadata"), dict)
            else dict(run.metadata),
        )
        pr_context = IssueToPRContext(**payload)
        return runtime.run_worker(self.stage, PRWorker.remote(), PRWorkerInput(context=pr_context))
