from typing import Any

from core.contracts.run_context import IssueToPRContext, PRToMergeContext
from core.orchestrator.coordinator import Coordinator
from core.orchestrator.models import RunModel, RunType
from infra.storage.artifacts import load_run, record_run_event
from infra.storage.review_requests import mark_review_request_applied

# Resuming an approved review re-enters the pipeline at the stage that asked for
# review (the publish step). This runs in the worker, which owns Ray and git, so
# the heavy publish/PR work happens in the properly provisioned environment.

def _build_issue_to_pr_context(run: RunModel, base: dict[str, Any]) -> IssueToPRContext:
    ctx = dict(base)
    ctx["repository"] = ctx.get("repository") or run.repository
    issue_number = ctx.get("issue_number") or run.issue_number
    if not isinstance(issue_number, int):
        raise ValueError("Missing issue_number for resume")
    ctx["issue_number"] = issue_number
    ctx["head_branch"] = ctx.get("head_branch") or f"autopr/issue-{issue_number}"
    ctx["base_branch"] = ctx.get("base_branch") or "main"
    ctx["review_approved"] = True
    ctx["execute_remote_actions"] = True
    ctx["metadata"] = ctx.get("metadata") if isinstance(ctx.get("metadata"), dict) else dict(run.metadata)
    return IssueToPRContext.model_validate(ctx)

def _build_pr_to_merge_context(run: RunModel, base: dict[str, Any]) -> PRToMergeContext:
    ctx = dict(base)
    ctx["repository"] = ctx.get("repository") or run.repository
    pr_number = ctx.get("pull_request_number") or run.pull_request_number
    if not isinstance(pr_number, int):
        raise ValueError("Missing pull_request_number for resume")
    ctx["pull_request_number"] = pr_number
    ctx["review_approved"] = True
    ctx["execute_remote_actions"] = True
    ctx["metadata"] = ctx.get("metadata") if isinstance(ctx.get("metadata"), dict) else dict(run.metadata)
    return PRToMergeContext.model_validate(ctx)

def resume_after_approval(
    *,
    request_id: str,
    run_id: str,
    stage_index: int,
    context: dict[str, Any],
    reviewer: str = "",
    reason: str = "",
) -> RunModel:
    stored = load_run(run_id)
    if stored is None:
        raise ValueError(f"Run not found: {run_id}")

    run_model = RunModel.model_validate(stored.payload)
    resume_context = dict(context)
    resume_context["_resume_stage_index"] = int(stage_index)

    coordinator = Coordinator(run_model)
    if run_model.run_type == RunType.ISSUE_TO_PR:
        final_run = coordinator.run_issue_to_pr(_build_issue_to_pr_context(run_model, resume_context))
    else:
        final_run = coordinator.run_pr_to_merge(_build_pr_to_merge_context(run_model, resume_context))

    mark_review_request_applied(
        request_id=request_id,
        execution_run_id=str(final_run.run_id),
    )
    record_run_event(
        run_id,
        "review_decision_applied",
        {
            "request_id": request_id,
            "decision": "approved",
            "decision_by": reviewer,
            "reason": reason,
            "final_state": final_run.state,
            "executed_in": "worker",
        },
    )
    return final_run
