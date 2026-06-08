from typing import Literal

from pydantic import BaseModel, Field
from core.contracts.enums import CheckStatus

class ReviewCheck(BaseModel):
    name: str = Field(..., description="Name of the review check.")
    status: CheckStatus = Field(..., description="Outcome of this review check.")
    details: str = Field(default="", description="Additional details for this check.")

class LLMBlockingFinding(BaseModel):
    severity: Literal["low", "medium", "high"] = "medium"
    category: str = Field(default="", description="Finding category.")
    file_path: str = Field(default="", description="Relevant file path, when known.")
    summary: str = Field(..., description="User-facing finding summary.")
    suggested_fix: str = Field(default="", description="Suggested remediation.")
    evidence: str = Field(default="", description="Short evidence excerpt or rationale.")

class LLMMergeRiskReview(BaseModel):
    merge_risk: Literal["low", "medium", "high"]
    confidence: Literal["low", "medium", "high"]
    summary: str = Field(default="", description="High-level LLM review summary.")
    blocking_findings: list[LLMBlockingFinding] = Field(default_factory=list)

class ReviewOutput(BaseModel):
    summary: str = Field(default="", description="High-level summary of review stage.")
    checks: list[ReviewCheck] = Field(default_factory=list, description="Review check results.")
    required_actions: list[str] = Field(default_factory=list, description="Actions needed before merge can proceed.")
    llm_review: LLMMergeRiskReview | None = Field(default=None, description="Advisory LLM merge-risk review.")
