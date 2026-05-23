from select import select
import uuid
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import select, update, insert

from infra.storage.engine import get_engine
from infra.storage.schema import review_requests

_ALLOWED_DECISIONS = {"approved", "disapproved"}

def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "run_id": row.run_id,
        "run_type": row.run_type,
        "stage": row.stage,
        "stage_index": row.stage_index,
        "status": row.status,
        "decision": row.decision or "",
        "decision_source": row.decision_source or "",
        "decision_by": row.decision_by or "",
        "reason": row.reason or "",
        "context": row.context or {},
        "slack_message_ref": row.slack_message_ref or "",
        "decided_at": row.decided_at or "",
        "applied_at": row.applied_at or "",
        "execution_run_id": row.execution_run_id or "",
        "created_at": row.created_at or "",
        "updated_at": row.updated_at or "",
    }

def get_review_request(request_id: str) -> dict[str, Any] | None:
    engine = get_engine()
    query = select(review_requests).where(
        review_requests.c.request_id == request_id
    )
    with engine.begin() as conn:
        res = conn.execute(query).fetchone()
        
    return _row_to_dict(res) if res else None

def create_review_request(
    *,
    run_id: str,
    run_type: str,
    stage: str,
    stage_index: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    engine = get_engine()
    query = insert(review_requests).values(
        request_id=request_id,
        run_id=run_id,
        run_type=run_type,
        stage=stage,
        stage_index=stage_index,
        context=context,
        status="pending",
    )

    with engine.begin() as conn:
        conn.execute(query)

    created = get_review_request(request_id)
    if created is None:
        raise RuntimeError("Failed to create review request")
    return created

def attach_review_request_slack_ref(request_id: str, message_ref: str) -> None:
    engine = get_engine()
    query = update(review_requests).where(
        review_requests.c.request_id == request_id
    ).values(
        slack_message_ref=message_ref.strip()
    )

    with engine.begin() as conn:
        conn.execute(query)

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

    engine = get_engine()
    query = update(review_requests).where(
        review_requests.c.request_id == request_id
    ).values(
        decision=normalized,
        decision_source=source.strip(),
        decision_by=decision_by.strip(),
        reason=reason.strip(),
        status="decided"
    )

    with engine.begin() as conn:
        conn.execute(query)

    updated = get_review_request(request_id)
    if updated is None:
        raise RuntimeError(f"Failed to update review request: {request_id}")
    return updated

def mark_review_request_applied(
    *,
    request_id: str,
    execution_run_id: str = "",
) -> dict[str, Any]:
    engine = get_engine()
    query = update(review_requests).where(
        review_requests.c.request_id == request_id
    ).values(
        status="applied",
        execution_run_id=execution_run_id.strip()
    )

    with engine.begin() as conn:
        conn.execute(query)

    updated = get_review_request(request_id)
    if updated is None:
        raise RuntimeError(f"Failed to mark review request applied: {request_id}")
    return updated