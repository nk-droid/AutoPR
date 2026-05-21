import json
import uuid
from typing import Any
from datetime import datetime, timezone

from infra.storage.db import get_db

_ALLOWED_DECISIONS = {"approved", "disapproved"}

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

def _json_loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "request_id": str(row["request_id"]),
        "run_id": str(row["run_id"]),
        "run_type": str(row["run_type"]),
        "stage": str(row["stage"]),
        "stage_index": int(row["stage_index"]),
        "status": str(row["status"]),
        "decision": str(row["decision"]),
        "decision_source": str(row["decision_source"]),
        "decision_by": str(row["decision_by"]),
        "reason": str(row["reason"]),
        "context": _json_loads(str(row["context_json"])),
        "slack_message_ref": str(row["slack_message_ref"]),
        "decided_at_utc": str(row["decided_at_utc"]),
        "applied_at_utc": str(row["applied_at_utc"]),
        "execution_run_id": str(row["execution_run_id"]),
        "created_at_utc": str(row["created_at_utc"]),
        "updated_at_utc": str(row["updated_at_utc"]),
    }

def get_review_request(request_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                request_id, run_id, run_type, stage, stage_index, status,
                decision, decision_source, decision_by, reason,
                context_json, slack_message_ref, decided_at_utc, applied_at_utc,
                execution_run_id, created_at_utc, updated_at_utc
            FROM review_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None

def create_review_request(
    *,
    run_id: str,
    run_type: str,
    stage: str,
    stage_index: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    now = _utc_now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO review_requests (
                request_id, run_id, run_type, stage, stage_index, status,
                decision, decision_source, decision_by, reason, context_json,
                slack_message_ref, decided_at_utc, applied_at_utc, execution_run_id,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                run_id,
                run_type,
                stage,
                int(stage_index),
                "pending",
                "",
                "",
                "",
                "",
                _json_dumps(context if isinstance(context, dict) else {}),
                "",
                "",
                "",
                "",
                now,
                now,
            ),
        )
    created = get_review_request(request_id)
    if created is None:
        raise RuntimeError("Failed to create review request")
    return created

def attach_review_request_slack_ref(request_id: str, message_ref: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE review_requests
            SET slack_message_ref = ?, updated_at_utc = ?
            WHERE request_id = ?
            """,
            (message_ref.strip(), _utc_now_iso(), request_id),
        )

def record_review_decision(
    *,
    request_id: str,
    decision: str,
    source: str = "slack_button",
    decision_by: str = "",
    reason: str = "",
) -> dict[str, Any]:
    normalized = decision.strip().lower()
    if normalized not in _ALLOWED_DECISIONS:
        raise ValueError(f"Invalid decision: {decision}")

    current = get_review_request(request_id)
    if current is None:
        raise ValueError(f"Review request not found: {request_id}")

    if current["status"] == "applied":
        return current

    if current["decision"] and current["decision"] != normalized:
        raise ValueError(
            f"Review request already decided as '{current['decision']}', cannot change to '{normalized}'."
        )

    if current["status"] == "decided" and current["decision"] == normalized:
        return current

    now = _utc_now_iso()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE review_requests
            SET
                status = ?,
                decision = ?,
                decision_source = ?,
                decision_by = ?,
                reason = ?,
                decided_at_utc = ?,
                updated_at_utc = ?
            WHERE request_id = ?
            """,
            (
                "decided",
                normalized,
                source.strip(),
                decision_by.strip(),
                reason.strip(),
                now,
                now,
                request_id,
            ),
        )

    updated = get_review_request(request_id)
    if updated is None:
        raise RuntimeError(f"Failed to update review request: {request_id}")
    return updated

def mark_review_request_applied(
    *,
    request_id: str,
    execution_run_id: str = "",
) -> dict[str, Any]:
    now = _utc_now_iso()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE review_requests
            SET
                status = ?,
                applied_at_utc = ?,
                execution_run_id = ?,
                updated_at_utc = ?
            WHERE request_id = ?
            """,
            ("applied", now, execution_run_id.strip(), now, request_id),
        )

    updated = get_review_request(request_id)
    if updated is None:
        raise RuntimeError(f"Failed to mark review request applied: {request_id}")
    return updated
