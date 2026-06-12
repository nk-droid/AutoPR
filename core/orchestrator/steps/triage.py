import os
from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.orchestrator.steps.base import PipelineStep, StepRuntime
from core.orchestrator.models import RunModel, StageResult, StageStatus
from core.contracts.run_context import TriageIssueInput, TriageWorkerInput

from infra.ray.actors import TriageWorker
from infra.github.issues import get_issue_details

from observability.tracing import pipeline_step_attrs, traced


class TriageStep(PipelineStep):
    stage = PipelineStage.TRIAGE
    success_state = RunState.TRIAGED.value

    @traced("pipeline.triage_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        issue_number = context.get("issue_number")
        repo = context.get("repository")
        issue = get_issue_details(
            issue_reference=issue_number,
            repo=repo,
            token=os.environ.get("GITHUB_TOKEN"),
        )

        title = issue.get("title", "")
        body = issue.get("body", "")
        if not title:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "Triage blocked: issue title is missing."},
            )

        triage_input = TriageWorkerInput(issue=TriageIssueInput(title=title, body=body))
        return runtime.run_worker(self.stage, TriageWorker.remote(), triage_input)
