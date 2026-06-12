import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunType(str, Enum):
    ISSUE_TO_PR = "ISSUE_TO_PR"
    PR_TO_MERGE = "PR_TO_MERGE"


class IssueActions(str, Enum):
    OPENED = "opened"
    REOPENED = "reopened"


class StageStatus(str, Enum):
    OK = "ok"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class TransitionEvent(BaseModel):
    from_state: str
    to_state: str
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StageResult(BaseModel):
    stage: str
    status: StageStatus = StageStatus.OK
    outputs: dict[str, Any] = Field(default_factory=dict)
    notes: dict[str, Any] = Field(default_factory=dict)


class PRDecision(BaseModel):
    allowed: bool
    reason: str = ""
    blocking_reasons: list[str] = Field(default_factory=list)


class MergeDecision(BaseModel):
    allowed: bool
    reason: str = ""
    blocking_reasons: list[str] = Field(default_factory=list)


class RunModel(BaseModel):
    run_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    state: str
    run_type: RunType = RunType.ISSUE_TO_PR
    repository: str = ""
    issue_number: int | None = None
    pull_request_number: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    stage_results: list[StageResult] = Field(default_factory=list)
    transition_history: list[TransitionEvent] = Field(default_factory=list)
