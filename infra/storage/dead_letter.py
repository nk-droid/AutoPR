from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from infra.storage.engine import get_engine
from infra.storage.schema import dead_letter_jobs

def record_dead_letter_job(
    *,
    message_id: str,
    kind: str,
    run_type: str,
    repository: str,
    attempts: int,
    last_error: str,
    payload: dict[str, Any],
) -> None:
    """Persist a job that exhausted its retries and landed in the DLQ."""
    engine = get_engine()
    now = datetime.now(timezone.utc)
    stmt = pg_insert(dead_letter_jobs).values(
        message_id=message_id,
        kind=kind,
        run_type=run_type,
        repository=repository,
        attempts=attempts,
        last_error=last_error[:2000],
        payload=payload,
        created_at=now,
        updated_at=now,
    )
    # A redrived job can land here again under the same message_id; keep latest.
    stmt = stmt.on_conflict_do_update(
        index_elements=["message_id"],
        set_={
            "attempts": stmt.excluded.attempts,
            "last_error": stmt.excluded.last_error,
            "payload": stmt.excluded.payload,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)
