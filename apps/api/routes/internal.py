from typing import Any
from fastapi import APIRouter, HTTPException, Query

from core.contracts.run_context import IssueToPRContext, PRToMergeContext
from core.orchestrator.coordinator import Coordinator
from core.orchestrator.models import RunModel, RunType
from infra.slack.notification import decode_review_action_token, send_review_decision_notification
from infra.storage.artifacts import load_run, record_run_event
from infra.storage.review_requests import (
    mark_review_request_applied,
    record_review_decision,
)

router = APIRouter(prefix="/internal", tags=["internal"])

@router.post("/agent-result")
def agent_result() -> dict:
    return {"status": "ok"}

def _build_issue_to_pr_context(run: RunModel, base: dict[str, Any]) -> IssueToPRContext:
    ctx = dict(base)
    ctx["repository"] = ctx.get("repository") or run.repository
    issue_number = ctx.get("issue_number") or run.issue_number
    if not isinstance(issue_number, int):
        raise HTTPException(status_code=400, detail="Missing issue_number for resume")
    ctx["issue_number"] = issue_number
    ctx["head_branch"] = ctx.get("head_branch") or f"autopr/issue-{issue_number}"
    ctx["base_branch"] = ctx.get("base_branch") or "main"
    ctx["execute_remote_actions"] = True
    ctx["metadata"] = ctx.get("metadata") if isinstance(ctx.get("metadata"), dict) else dict(run.metadata)
    return IssueToPRContext.model_validate(ctx)

def _build_pr_to_merge_context(run: RunModel, base: dict[str, Any]) -> PRToMergeContext:
    ctx = dict(base)
    ctx["repository"] = ctx.get("repository") or run.repository
    pr_number = ctx.get("pull_request_number") or run.pull_request_number
    if not isinstance(pr_number, int):
        raise HTTPException(status_code=400, detail="Missing pull_request_number for resume")
    ctx["pull_request_number"] = pr_number
    ctx["review_approved"] = True
    ctx["execute_remote_actions"] = True
    ctx["metadata"] = ctx.get("metadata") if isinstance(ctx.get("metadata"), dict) else dict(run.metadata)
    return PRToMergeContext.model_validate(ctx)

@router.get("/review/decision")
def review_decision(
    token: str = Query(...),
    reviewer: str = Query(default=""),
    reason: str = Query(default=""),
) -> dict[str, Any]:
    try:
        request_id, decision = decode_review_action_token(token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid token: {exc}") from exc

    request = record_review_decision(
        request_id=request_id,
        decision=decision,
        source="slack_button",
        decision_by=reviewer,
        reason=reason,
    )

    slack_result = send_review_decision_notification(
        request_id=request_id,
        decision=decision,
        reviewer=reviewer,
        reason=reason,
    )

    if request["decision"] == "disapproved":
        mark_review_request_applied(request_id=request_id)
        record_run_event(
            request["run_id"],
            "review_decision_applied",
            {
                "request_id": request_id,
                "decision": "disapproved",
                "decision_by": reviewer,
                "reason": reason,
                "slack_sent": bool(slack_result.get("sent", False)),
            },
        )
        return {
            "status": "ok",
            "request_id": request_id,
            "decision": "disapproved",
            "action": "run_stopped",
            "slack_sent": bool(slack_result.get("sent", False)),
        }

    stored = load_run(request["run_id"])
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {request['run_id']}")

    run_model = RunModel.model_validate(stored.payload)
    resume_context = dict(request.get("context", {}))
    resume_context["_resume_stage_index"] = int(request.get("stage_index", 0))

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
        request["run_id"],
        "review_decision_applied",
        {
            "request_id": request_id,
            "decision": "approved",
            "decision_by": reviewer,
            "reason": reason,
            "final_state": final_run.state,
            "slack_sent": bool(slack_result.get("sent", False)),
        },
    )

    return {
        "status": "ok",
        "request_id": request_id,
        "decision": "approved",
        "action": "run_resumed",
        "run_id": str(final_run.run_id),
        "final_state": final_run.state,
        "slack_sent": bool(slack_result.get("sent", False)),
    }
