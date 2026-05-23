import uuid
from sqlalchemy import func, UniqueConstraint
from sqlalchemy import ForeignKey
from sqlalchemy import DateTime
from sqlalchemy import Enum, MetaData, Table, Column, Integer, String, JSON

from core.contracts.enums import RunState
from core.orchestrator.models import RunType

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

# Initialize database
from infra.storage.engine import get_engine

engine = get_engine()
metadata.create_all(engine)