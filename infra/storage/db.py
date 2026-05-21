import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = "data/autopr.sqlite3"

def resolve_db_path() -> Path:
    path_value = os.getenv("AUTOPR_DB_PATH", DEFAULT_DB_PATH)
    path = Path(path_value).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def _ensure_schema(conn: sqlite3.Connection) -> None:
    # Tables are created lazily so local development can bootstrap from scratch.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            run_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            run_id TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            PRIMARY KEY (run_id, artifact_key),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_run_events_run_id
        ON run_events (run_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_artifacts_run_id
        ON artifacts (run_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_requests (
            request_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            run_type TEXT NOT NULL,
            stage TEXT NOT NULL,
            stage_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            decision TEXT NOT NULL,
            decision_source TEXT NOT NULL,
            decision_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            context_json TEXT NOT NULL,
            slack_message_ref TEXT NOT NULL,
            decided_at_utc TEXT NOT NULL,
            applied_at_utc TEXT NOT NULL,
            execution_run_id TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_review_requests_run_id
        ON review_requests (run_id)
        """
    )


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(resolve_db_path()))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(connection)
    try:
        yield connection
        # Commit once per context to keep write operations atomic.
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
