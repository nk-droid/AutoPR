from typing import Literal
from pydantic import BaseModel, Field

class QACheck(BaseModel):
    name: str = Field(..., description="Name of the QA check.")
    status: Literal["pass", "warn", "fail"] = Field(
        ...,
        description="Result of the QA check.",
    )
    details: str = Field(default="", description="Additional details for this check.")

class QAOutput(BaseModel):
    summary: str = Field(default="", description="High-level QA summary.")
    checks: list[QACheck] = Field(default_factory=list, description="Check-level QA results.")
