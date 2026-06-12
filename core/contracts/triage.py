from pydantic import BaseModel, Field
from typing import List
from core.contracts.enums import RiskLevel
from core.orchestrator.models import StageStatus

class TaskSpec(BaseModel):
    problem: str = Field(..., description="A clear and concise description of the problem to be solved.")
    acceptance_criteria: List[str] = Field(..., description="List of criteria that must be met for the task to be considered complete.")
    constraints: List[str] = Field(..., description="List of constraints that must be adhered to.")
    out_of_scope: List[str] = Field(..., description="List of items that are explicitly out of scope for this task.")

class Risk(BaseModel):
    level: RiskLevel = Field(..., description="The level of risk associated with the task.")
    reasons: List[str] = Field(..., description="List of reasons for the assigned risk level.")

class AmbiguityResult(BaseModel):
    status: StageStatus = Field(..., description="Indicates whether the task specification is clear (ok) or if it requires human intervention due to ambiguities.")
    questions: List[str] = Field(default_factory=list, description="List of questions that highlight potential ambiguities in the task specification.")

class TriageResult(BaseModel):
    task_spec: TaskSpec = Field(..., description="The specification of the task being triaged.")
    risk: Risk = Field(..., description="The risk assessment for the task.")
    ambiguity: AmbiguityResult = Field(..., description="The ambiguity assessment for the task.")
    questions: List[str] = Field(default_factory=list, description="List of questions to be asked during the triage process.")
