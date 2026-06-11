from core.orchestrator.models import RunType

from core.orchestrator.steps.base import PipelineStep
from core.orchestrator.steps.code import CodeStep
from core.orchestrator.steps.merge import MergeStep
from core.orchestrator.steps.plan import PlanStep
from core.orchestrator.steps.pr_open import PROpenStep
from core.orchestrator.steps.prepare import PrepareStep
from core.orchestrator.steps.publish import PublishStep
from core.orchestrator.steps.qa import QAStep
from core.orchestrator.steps.review import ReviewStep
from core.orchestrator.steps.triage import TriageStep

def steps_for_run_type(run_type: RunType) -> list[PipelineStep]:
    """
    Build the ordered pipeline steps for the selected workflow type.

    Args:
        run_type: Workflow type requested by the coordinator.

    Returns:
        Ordered pipeline steps that should execute for the workflow.
    """

    if run_type == RunType.ISSUE_TO_PR:
        # Ordered pipeline from intake to PR open.
        return [
            TriageStep(),
            PrepareStep(),
            PlanStep(),
            CodeStep(),
            QAStep(),
            PublishStep(),
            PROpenStep(),
            ReviewStep(),
            MergeStep(),
        ]
    if run_type == RunType.PR_TO_MERGE:
        # Merge pipeline assumes PR already exists.
        return [
            ReviewStep(),
            MergeStep(),
        ]
    raise ValueError(f"Unsupported run type: {run_type}")
