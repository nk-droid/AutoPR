from typing import Literal
from pydantic import BaseModel, Field

class ReviewCheck(BaseModel):
    name: str = Field(..., description="Name of the review check.")
    status: Literal["pass", "warn", "fail"] = Field(..., description="Outcome of this review check.")
    details: str = Field(default="", description="Additional details for this check.")

class ReviewOutput(BaseModel):
    summary: str = Field(default="", description="High-level summary of review stage.")
    checks: list[ReviewCheck] = Field(default_factory=list, description="Review check results.")
    required_actions: list[str] = Field(default_factory=list, description="Actions needed before merge can proceed.")

class MergeOutput(BaseModel):
    merged: bool = Field(default=False, description="Whether merge succeeded.")
    message: str = Field(default="", description="Merge result message.")
    merge_sha: str = Field(default="", description="Merge commit SHA when available.")
    notes: dict = Field(default_factory=dict, description="Additional merge metadata.")
