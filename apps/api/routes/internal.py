from typing import Any
from fastapi import APIRouter, HTTPException, Query

from apps.api.routes.webhooks import get_webhook_queue
from core.orchestrator.models import RunModel
from core.policies.comments import format_review_findings_comment, normalize_public_findings
from infra.github.client import GitHubClient
from infra.slack.notification import decode_review_action_token, send_review_decision_notification
from infra.storage.artifacts import load_run, record_run_event
from infra.storage.review_requests import (
    get_review_request,
    mark_review_request_applied,
    record_review_decision,
)

router = APIRouter(prefix="/internal", tags=["internal"])


def _comment_on_disapproved_review(context: dict[str, Any]) -> str:
    repository = context.get("repository")
    pull_request_number = context.get("pull_request_number")
    if not isinstance(repository, str) or not repository:
        return "missing_repository"
    if not isinstance(pull_request_number, int):
        return "missing_pull_request_number"

    findings = normalize_public_findings(context.get("blocking_findings"))
    if not findings and isinstance(context.get("llm_review"), dict):
        findings = normalize_public_findings(context["llm_review"].get("blocking_findings"))

    body = format_review_findings_comment(
        title="AutoPR did not merge this pull request after review.",
        findings=findings,
        fallback="A reviewer did not approve the merge. Please inspect the pull request and address the review feedback before trying again.",
    )
    client = GitHubClient()
    try:
        client.comment_on_pull_request(repo=repository, pull_number=pull_request_number, body=body)
    except Exception as exc:
        return str(exc)
    finally:
        client.close()
    return ""


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
        comment_error = _comment_on_disapproved_review(dict(request.get("context", {})))
        mark_review_request_applied(request_id=request_id)
        event_payload = {
            "request_id": request_id,
            "decision": "disapproved",
            "decision_by": reviewer,
            "reason": reason,
            "slack_sent": slack_sent,
            "comment_added": not bool(comment_error),
        }
        if comment_error:
            event_payload["comment_error"] = comment_error
        record_run_event(request["run_id"], "review_decision_applied", event_payload)
        return {
            "status": "ok",
            "request_id": request_id,
            "decision": "disapproved",
            "action": "run_stopped",
            "slack_sent": slack_sent,
            "comment_added": not bool(comment_error),
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
