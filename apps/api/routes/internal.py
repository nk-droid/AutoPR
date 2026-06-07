from typing import Any
from fastapi import APIRouter, HTTPException, Query

from apps.api.routes.webhooks import get_webhook_queue
from core.orchestrator.models import RunModel
from infra.slack.notification import decode_review_action_token, send_review_decision_notification
from infra.storage.artifacts import load_run, record_run_event
from infra.storage.review_requests import (
    get_review_request,
    mark_review_request_applied,
    record_review_decision,
)

router = APIRouter(prefix="/internal", tags=["internal"])

@router.post("/agent-result")
def agent_result() -> dict:
    return {"status": "ok"}

@router.get("/review/decision")
async def review_decision(
    token: str = Query(...),
    reviewer: str = Query(default=""),
    reason: str = Query(default=""),
) -> dict[str, Any]:
    try:
        request_id, decision = decode_review_action_token(token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid token: {exc}") from exc

    existing = get_review_request(request_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Review request not found: {request_id}")
    already_processed = existing["status"] in {"decided", "applied"}

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
    slack_sent = bool(slack_result.get("sent", False))

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
                "slack_sent": slack_sent,
            },
        )
        return {
            "status": "ok",
            "request_id": request_id,
            "decision": "disapproved",
            "action": "run_stopped",
            "slack_sent": slack_sent,
        }

    if already_processed:
        return {
            "status": "ok",
            "request_id": request_id,
            "decision": "approved",
            "action": "already_processed",
            "slack_sent": slack_sent,
        }

    stored = load_run(request["run_id"])
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {request['run_id']}")
    run_model = RunModel.model_validate(stored.payload)

    queue = get_webhook_queue()
    message_id = await queue.enqueue_resume(
        run_type=run_model.run_type,
        run_id=request["run_id"],
        request_id=request_id,
        stage_index=int(request.get("stage_index", 0)),
        context=dict(request.get("context", {})),
    )

    record_run_event(
        request["run_id"],
        "review_decision_enqueued",
        {
            "request_id": request_id,
            "decision": "approved",
            "decision_by": reviewer,
            "reason": reason,
            "message_id": message_id,
            "slack_sent": slack_sent,
        },
    )

    return {
        "status": "ok",
        "request_id": request_id,
        "decision": "approved",
        "action": "run_resume_enqueued",
        "message_id": message_id,
        "slack_sent": slack_sent,
    }
