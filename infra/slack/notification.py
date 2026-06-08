import os
import hmac
import time
import base64
import hashlib
import requests
from typing import Any

from core.orchestrator.models import RunModel, StageResult

from dotenv import load_dotenv
load_dotenv()

_ALLOWED_DECISIONS = {"approved", "disapproved"}

def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

def _b64url_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> str:
    padding = "=" * ((4 - (len(value) % 4)) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8")).decode("utf-8")

def _token_secret() -> str:
    secret = os.getenv("REVIEW_ACTION_TOKEN_SECRET")
    if not secret:
        raise ValueError("REVIEW_ACTION_TOKEN_SECRET is not configured")
    return secret

def build_review_action_token(request_id: str, decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized not in _ALLOWED_DECISIONS:
        raise ValueError(f"Invalid decision: {decision}")

    ttl_sec = int(os.getenv("REVIEW_ACTION_TTL_SEC", 604800))

    expires_at = int(time.time()) + ttl_sec
    payload = f"{request_id}|{normalized}|{expires_at}"
    digest = hmac.new(_token_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{_b64url_encode(payload)}.{digest}"

def decode_review_action_token(token: str) -> tuple[str, str]:
    encoded, sep, signature = token.partition(".")
    if not sep:
        raise ValueError("Invalid token format")

    payload = _b64url_decode(encoded)
    expected = hmac.new(_token_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid token signature")

    request_id, decision, expires_raw = payload.split("|", 2)
    if decision not in _ALLOWED_DECISIONS:
        raise ValueError("Invalid decision in token")

    if int(expires_raw) < int(time.time()):
        raise ValueError("Token expired")

    return request_id, decision


def _action_base_url() -> str:
    base = os.getenv("AUTOPR_PUBLIC_BASE_URL", "http://localhost:8000")
    return base.rstrip("/")


def _build_action_url(request_id: str, decision: str) -> str:
    token = build_review_action_token(request_id, decision)
    return f"{_action_base_url()}/internal/review/decision?token={token}"


def _extract_reason(result: StageResult) -> str:
    notes = result.notes if isinstance(result.notes, dict) else {}
    for key in ("reason", "blocking_reason", "error"):
        value = notes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    outputs = result.outputs if isinstance(result.outputs, dict) else {}
    summary = outputs.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    return "No reason provided."

def _format_blocking_findings(value: Any, *, limit: int = 5) -> str:
    if not isinstance(value, list):
        return ""

    lines: list[str] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") or item.get("reason")
        if not isinstance(summary, str) or not summary.strip():
            continue
        severity = item.get("severity")
        prefix = f"`{severity}` " if isinstance(severity, str) and severity.strip() else ""
        lines.append(f"- {prefix}{summary.strip()}")
    return "\n".join(lines)

def send_needs_review_notification(
    run: RunModel,
    result: StageResult,
    review_result: dict[str, Any]
) -> dict[str, Any]:
    
    if not _env_flag("SLACK_NOTIFY_NEEDS_REVIEW", True):
        return {
            "sent": False,
            "message_ref": "",
            "reason": "disabled"
        }
    
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return {
            "sent": False,
            "message_ref": "",
            "reason": "missing_webhook"
        }
    
    request_id = review_result.get("request_id", "")
    if not request_id:
        return {
            "sent": False,
            "message_ref": "",
            "reason": "missing_request_id"
        }
    
    run_id = str(run.run_id)
    stage = str(result.stage)
    repository = run.repository
    reason = _extract_reason(result)
    notes = result.notes if isinstance(result.notes, dict) else {}
    merge_risk = notes.get("merge_risk")
    confidence = notes.get("confidence")
    findings_text = _format_blocking_findings(notes.get("blocking_findings"))
    
    approve_url = _build_action_url(request_id, "approved")
    disapprove_url = _build_action_url(request_id, "disapproved")
    run_url = f"{_action_base_url()}/runs/{run_id}"

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "AutoPR needs review"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Repository*\n`{repository}`"},
                {"type": "mrkdwn", "text": f"*Stage*\n`{stage}`"},
                {"type": "mrkdwn", "text": f"*Run ID*\n`{run_id}`"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason*\n{reason}"}},
    ]
    if isinstance(merge_risk, str) and merge_risk.strip():
        confidence_text = confidence if isinstance(confidence, str) and confidence.strip() else "unknown"
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Merge risk*\n`{merge_risk}`"},
                    {"type": "mrkdwn", "text": f"*Confidence*\n`{confidence_text}`"},
                ],
            }
        )
    if findings_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Blocking findings*\n{findings_text}"}})
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "url": approve_url,
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Disapprove"},
                    "url": disapprove_url,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Run"},
                    "url": run_url,
                },
            ],
        }
    )

    payload = {
        "text": f"AutoPR needs review for {repository} [{stage}]",
        "blocks": blocks,
    }

    timeout_sec = int(os.getenv("SLACK_TIMEOUT_SEC", 5))

    response = requests.post(
        webhook_url,
        json=payload,
        timeout=timeout_sec
    )

    if response.status_code >= 400:
        return {
            "sent": False,
            "message_ref": "",
            "reason": f"slack_http_{response.status_code}",
            "response_text": response.text[:500]
        }
    
    # Incoming webhook does not return Slack message; store a synthetic reference.
    synthetic_ref = f"{run_id}:{stage}:{int(time.time())}"
    return {"sent": True, "message_ref": synthetic_ref, "reason": "ok"}

def send_review_decision_notification(
    *,
    request_id: str,
    decision: str,
    reviewer: str = "",
    reason: str = "",
) -> dict[str, Any]:
    normalized = decision.strip().lower()
    if normalized not in _ALLOWED_DECISIONS:
        return {
            "sent": False,
            "reason": "invalid_decision",
        }

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return {
            "sent": False,
            "reason": "missing_webhook",
        }

    title = "Approved" if normalized == "approved" else "Disapproved"
    reviewer_text = reviewer.strip() if isinstance(reviewer, str) else ""
    reason_text = reason.strip() if isinstance(reason, str) else ""

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"AutoPR decision: {title}"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Request ID*\n`{request_id}`"},
                {"type": "mrkdwn", "text": f"*Decision*\n`{normalized}`"},
            ],
        },
    ]

    if reviewer_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Reviewer*\n{reviewer_text}"}})

    if reason_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason*\n{reason_text}"}})

    payload = {
        "text": f"AutoPR decision: {title} ({request_id})",
        "blocks": blocks,
    }

    timeout_sec = int(os.getenv("SLACK_TIMEOUT_SEC", 5))
    response = requests.post(webhook_url, json=payload, timeout=timeout_sec)

    if response.status_code >= 400:
        return {
            "sent": False,
            "reason": f"slack_http_{response.status_code}",
            "response_text": response.text[:500],
        }

    return {
        "sent": True,
        "reason": "ok",
    }

if __name__ == "__main__":
    import uuid

    run = RunModel(
        run_id=uuid.UUID("4bf96c14-423f-431c-b172-b6e74585176a"),
        state="test",
        issue_number=1,
        pull_request_number=1,
    )

    result = StageResult(
        stage="test_stage",
    )

    review_result = {
        "request_id": "test_id"
    }

    send_needs_review_notification(run, result, review_result)