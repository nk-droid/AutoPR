from core.orchestrator.models import RunType

from core.orchestrator.steps.agentic import CodeStep, PlanStep, PROpenStep, QAStep, ReviewStep, TriageStep
from core.orchestrator.steps.base import PipelineStep
from core.orchestrator.steps.merge import MergeStep
from core.orchestrator.steps.publish import PublishStep

def steps_for_run_type(run_type: RunType) -> list[PipelineStep]:
    if run_type == RunType.ISSUE_TO_PR:
        # Ordered pipeline from intake to PR open.
        return [
            TriageStep(),
            PlanStep(),
            CodeStep(),
            QAStep(),
            PublishStep(),
            PROpenStep(),
        ]
    if run_type == RunType.PR_TO_MERGE:
        # Merge pipeline assumes PR already exists.
        return [
            ReviewStep(),
            MergeStep(),
        ]
    raise ValueError(f"Unsupported run type: {run_type}")
