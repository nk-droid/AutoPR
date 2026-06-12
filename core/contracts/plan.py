from typing import List
import uuid
from pydantic import BaseModel, Field
from core.contracts.enums import RiskLevel


class PlanStep(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    title: str = Field(..., description="Short name of the step.")
    objective: str = Field(..., description="What this step achieves.")
    rationale: str = Field(default="", description="Why this step is needed.")
    files: List[str] = Field(
        default_factory=list, description="Files expected to be touched in this step."
    )
    tests: List[str] = Field(default_factory=list, description="Tests to run or add for this step.")
    dependencies: List[uuid.UUID] = Field(
        default_factory=list, description="Step ids that must complete before this step."
    )
    acceptance_criteria: List[str] = Field(
        default_factory=list, description="Conditions that indicate this step is complete."
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW, description="Estimated risk for this step."
    )


class PlanOutput(BaseModel):
    strategy: str = Field(..., description="High-level implementation strategy.")
    steps: List[PlanStep] = Field(default_factory=list, description="Ordered execution steps.")
    assumptions: List[str] = Field(
        default_factory=list, description="Assumptions made while planning."
    )
    open_questions: List[str] = Field(
        default_factory=list, description="Questions needing clarification."
    )
