import json
from datetime import datetime, timezone
from typing import Any
from infra.storage.db import get_db
from infra.storage.models import StoredArtifact, StoredRun, StoredRunEvent

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

def _json_loads(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}

def upsert_run(
    *,
    run_id: str,
    state: str,
    run_type: str,
    payload: dict[str, Any],
) -> None:
    now = _utc_now_iso()
    with get_db() as conn:
        # Upsert keeps one canonical row per run while preserving created_at.
        conn.execute(
            """
            INSERT INTO runs (
                run_id,
                state,
                run_type,
                payload_json,
                created_at_utc,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                state = excluded.state,
                run_type = excluded.run_type,
                payload_json = excluded.payload_json,
                updated_at_utc = excluded.updated_at_utc
            """,
            (run_id, state, run_type, _json_dumps(payload), now, now),
        )

def record_run_event(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO run_events (
                run_id,
                event_type,
                payload_json,
                created_at_utc
            ) VALUES (?, ?, ?, ?)
            """,
            (run_id, event_type, _json_dumps(payload), _utc_now_iso()),
        )

def save_artifact(run_id: str, key: str, value: dict) -> dict:
    now = _utc_now_iso()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO artifacts (
                run_id,
                artifact_key,
                value_json,
                updated_at_utc
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, artifact_key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at_utc = excluded.updated_at_utc
            """,
            (run_id, key, _json_dumps(value), now),
        )
    return {"run_id": run_id, "key": key, "saved": True}

def _load_events(run_id: str) -> list[StoredRunEvent]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, run_id, event_type, payload_json, created_at_utc
            FROM run_events
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
    return [
        StoredRunEvent(
            id=int(row["id"]),
            run_id=str(row["run_id"]),
            event_type=str(row["event_type"]),
            payload=_json_loads(str(row["payload_json"])),
            created_at_utc=str(row["created_at_utc"]),
        )
        for row in rows
    ]

def _load_artifacts(run_id: str) -> list[StoredArtifact]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT run_id, artifact_key, value_json, updated_at_utc
            FROM artifacts
            WHERE run_id = ?
            ORDER BY artifact_key ASC
            """,
            (run_id,),
        ).fetchall()
    return [
        StoredArtifact(
            run_id=str(row["run_id"]),
            key=str(row["artifact_key"]),
            value=_json_loads(str(row["value_json"])),
            updated_at_utc=str(row["updated_at_utc"]),
        )
        for row in rows
    ]

def load_run(run_id: str) -> StoredRun | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT run_id, state, run_type, payload_json, created_at_utc, updated_at_utc
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        return None

    # Events and artifacts are loaded separately to keep run row writes simple.
    return StoredRun(
        run_id=str(row["run_id"]),
        state=str(row["state"]),
        run_type=str(row["run_type"]),
        payload=_json_loads(str(row["payload_json"])),
        created_at_utc=str(row["created_at_utc"]),
        updated_at_utc=str(row["updated_at_utc"]),
        artifacts=_load_artifacts(run_id),
        events=_load_events(run_id),
    )
