from pydantic import BaseModel, Field
from core.contracts.enums import CheckStatus
from core.orchestrator.models import StageStatus


class QACheck(BaseModel):
    name: str = Field(..., description="Name of the QA check.")
    status: CheckStatus = Field(..., description="Result of the QA check.")
    details: dict = Field(default={}, description="Additional details for this check.")


class QAOutput(BaseModel):
    status: StageStatus = Field(default=StageStatus.BLOCKED)
    summary: str = Field(default="", description="High-level QA summary.")
    checks: list[QACheck] = Field(default_factory=list, description="Check-level QA results.")
    notes: dict[str, object] = Field(default_factory=dict)
