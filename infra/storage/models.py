from typing import Any
from pydantic import BaseModel, Field


class StoredArtifact(BaseModel):
    run_id: str
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    updated_at_utc: str


class StoredRunEvent(BaseModel):
    id: str
    run_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at_utc: str


class StoredRun(BaseModel):
    run_id: str
    state: str
    run_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at_utc: str
    updated_at_utc: str
    artifacts: list[StoredArtifact] = Field(default_factory=list)
    events: list[StoredRunEvent] = Field(default_factory=list)
