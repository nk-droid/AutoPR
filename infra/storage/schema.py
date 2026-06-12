import uuid
from sqlalchemy import Enum, MetaData, Table, Column, Integer, String, JSON
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import text

from core.contracts.enums import RunState
from core.orchestrator.models import RunType
from infra.storage.engine import get_engine

metadata = MetaData()

run_states = Enum(RunState, name="run_state")
run_types = Enum(RunType, name="run_type")

runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("state", run_states),
    Column("run_type", run_types),
    Column("repository", String),
    Column("issue_number", Integer),
    Column("pull_request_number", Integer),
    Column("payload", JSON),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

run_events = Table(
    "run_events",
    metadata,
    Column("id", String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("run_id", String, ForeignKey("runs.run_id")),
    Column("event_type", String),
    Column("payload", JSON),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("id", String, primary_key=True, default=lambda: str(uuid.uuid4())),
    Column("run_id", String, ForeignKey("runs.run_id")),
    Column("key", String, nullable=False),
    Column("value", JSON),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
    UniqueConstraint("run_id", "key", name="uq_run_artifact"),
)

review_requests = Table(
    "review_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("run_id", String, ForeignKey("runs.run_id")),
    Column("run_type", run_types),
    Column("stage", String),
    Column("stage_index", Integer),
    Column("status", String),
    Column("decision", String),
    Column("decision_source", String),
    Column("decision_by", String),
    Column("reason", String),
    Column("context", JSON),
    Column("slack_message_ref", String),
    Column("decided_at", DateTime, nullable=True),
    Column("applied_at", DateTime, nullable=True),
    Column("execution_run_id", String, ForeignKey("runs.run_id")),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

dead_letter_jobs = Table(
    "dead_letter_jobs",
    metadata,
    Column("message_id", String, primary_key=True),
    Column("kind", String),
    Column("run_type", String),
    Column("repository", String),
    Column("attempts", Integer),
    Column("last_error", String),
    Column("payload", JSON),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

# Stable key for the advisory lock guarding concurrent schema creation.
_SCHEMA_INIT_LOCK_KEY = 0x4155544F5052  # "AUTOPR"


def _init_schema(target_engine) -> None:
    if target_engine.dialect.name == "postgresql":
        with target_engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_INIT_LOCK_KEY})
            metadata.create_all(conn, checkfirst=True)
    else:
        metadata.create_all(target_engine, checkfirst=True)


engine = get_engine()
_init_schema(engine)
