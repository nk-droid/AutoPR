from pydantic import BaseModel, Field
from core.contracts.enums import CheckStatus

class ReviewCheck(BaseModel):
    name: str = Field(..., description="Name of the review check.")
    status: CheckStatus = Field(..., description="Outcome of this review check.")
    details: str = Field(default="", description="Additional details for this check.")

class ReviewOutput(BaseModel):
    summary: str = Field(default="", description="High-level summary of review stage.")
    checks: list[ReviewCheck] = Field(default_factory=list, description="Review check results.")
    required_actions: list[str] = Field(default_factory=list, description="Actions needed before merge can proceed.")
