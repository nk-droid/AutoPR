import logging
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from infra.storage.engine import get_engine
from infra.storage.schema import artifacts, runs, run_events
from infra.storage.models import StoredRunEvent, StoredArtifact, StoredRun

logger = logging.getLogger(__name__)


def upsert_run(
    *,
    run_id: str,
    state: str,
    run_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Persist the latest run snapshot for recovery and status lookup.

    Args:
        run_id: Stable run identifier.
        state: Current workflow state.
        run_type: Workflow type associated with the run.
        payload: Serialized run model payload.
    """

    engine = get_engine()
    # Perform an atomic upsert on postgres conflict
    stmt = pg_insert(runs).values(
        run_id=run_id,
        state=state,
        run_type=run_type,
        payload=payload,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["run_id"],
        set_={
            "state": stmt.excluded.state,
            "run_type": stmt.excluded.run_type,
            "payload": stmt.excluded.payload,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    # engine.begin() automatically commits transaction upon completion
    try:
        with engine.begin() as conn:
            conn.execute(stmt)
    except Exception as exc:
        logger.error(
            "run snapshot persist failed",
            extra={
                "event": "storage_write_failed",
                "operation": "upsert_run",
                "run_id": run_id,
                "state": state,
                "error": exc.__class__.__name__,
            },
        )
        raise


def record_run_event(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """
    Append an immutable event to a run's audit timeline.

    Args:
        run_id: Run identifier that owns the event.
        event_type: Event name describing the workflow occurrence.
        payload: Structured event context for audit and debugging.
    """

    engine = get_engine()
    query = run_events.insert().values(
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )
    try:
        with engine.begin() as conn:
            conn.execute(query)
    except Exception as exc:
        logger.error(
            "run event persist failed",
            extra={
                "event": "storage_write_failed",
                "operation": "record_run_event",
                "run_id": run_id,
                "run_event_type": event_type,
                "error": exc.__class__.__name__,
            },
        )
        raise


def save_artifact(run_id: str, key: str, value: dict) -> dict:
    """
    Upsert a named artifact produced during a pipeline run.

    Args:
        run_id: Run identifier that owns the artifact.
        key: Artifact key unique within the run.
        value: Structured artifact payload to persist.

    Returns:
        Small confirmation payload identifying the saved artifact.
    """

    engine = get_engine()
    # Perform an atomic upsert on conflict
    stmt = pg_insert(artifacts).values(
        run_id=run_id,
        key=key,
        value=value,
        updated_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_run_artifact",
        set_={
            "value": stmt.excluded.value,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    try:
        with engine.begin() as conn:
            conn.execute(stmt)
    except Exception as exc:
        logger.error(
            "artifact persist failed",
            extra={
                "event": "storage_write_failed",
                "operation": "save_artifact",
                "run_id": run_id,
                "artifact_key": key,
                "error": exc.__class__.__name__,
            },
        )
        raise

    return {"run_id": run_id, "key": key, "saved": True}


def _load_events(run_id: str) -> list[StoredRunEvent]:
    """
    Load persisted audit events for a run.

    Args:
        run_id: Run identifier whose events should be fetched.

    Returns:
        Stored run events in database row order.
    """

    engine = get_engine()
    query = select(run_events).where(run_events.c.run_id == run_id)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    return [
        StoredRunEvent(
            id=str(row.id),
            run_id=str(row.run_id),
            event_type=str(row.event_type),
            payload=row.payload,
            created_at_utc=str(row.created_at),
        )
        for row in rows
    ]


def _load_artifacts(run_id: str) -> list[StoredArtifact]:
    """
    Load persisted artifacts attached to a run.

    Args:
        run_id: Run identifier whose artifacts should be fetched.

    Returns:
        Stored artifacts attached to the run.
    """

    engine = get_engine()
    query = artifacts.select().where(artifacts.c.run_id == run_id)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    return [
        StoredArtifact(
            run_id=str(row.run_id),
            key=str(row.key),
            value=row.value,
            updated_at_utc=str(row.updated_at),
        )
        for row in rows
    ]


def load_run(run_id: str) -> StoredRun | None:
    """
    Load a run snapshot with its artifacts and event timeline.

    Args:
        run_id: Run identifier to load from storage.

    Returns:
        Stored run aggregate, or None when the run does not exist.
    """

    engine = get_engine()
    query = runs.select().where(runs.c.run_id == run_id)
    with engine.connect() as conn:
        row = conn.execute(query).fetchone()
    if row is None:
        return None

    return StoredRun(
        run_id=str(row.run_id),
        state=str(row.state),
        run_type=str(row.run_type),
        payload=row.payload,
        created_at_utc=str(row.created_at),
        updated_at_utc=str(row.updated_at),
        artifacts=_load_artifacts(run_id),
        events=_load_events(run_id),
    )
