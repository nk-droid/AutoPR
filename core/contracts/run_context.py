from typing import Any, Optional
from pydantic import BaseModel, ConfigDict

from core.contracts.code import CodeOutput
from core.contracts.enums import CheckStatus
from core.contracts.plan import PlanStep
from core.contracts.triage import TriageResult

class IssueToPRContext(BaseModel):
    model_config = ConfigDict(extra="allow")
    repository: str
    issue_number: int
    execute_remote_actions: bool
    head_branch: str
    base_branch: str
    metadata: dict[str, Any]

class PRToMergeContext(BaseModel):
    model_config = ConfigDict(extra="allow")
    repository: str
    pull_request_number: int
    review_approved: bool
    execute_remote_actions: bool
    metadata: dict[str, Any]

class TriageIssueInput(BaseModel):
    title: str
    body: Optional[str] = ""

class TriageWorkerInput(BaseModel):
    issue: TriageIssueInput

class PlanWorkerInput(BaseModel):
    triage_result: TriageResult
    repo_map: str = ""

class CodeWorkerInput(BaseModel):
    step: PlanStep
    repo_map: str
    file_contents: dict[str, str]
    dependency_files: dict[str, str] = {}
    qa_feedback: str = ""

class QAJobPayload(BaseModel):
    coding_output: CodeOutput
    coding_step: PlanStep
    repo_path: str | None
    qa_timeout_sec: int
    coverage_threshold: float

class ToolRunResult(BaseModel):
    name: str
    status: CheckStatus
    payload: dict[str, Any]

class QAWorkerInput(BaseModel):
    coding_output: CodeOutput
    coding_step: PlanStep
    tool_results: list[ToolRunResult]

class PRWorkerInput(BaseModel):
    context: IssueToPRContext

class ReviewWorkerInput(BaseModel):
    context: PRToMergeContext

class PublishWorkerInput(BaseModel):
    context: dict[str, Any]

class MergeWorkerInput(BaseModel):
    context: dict[str, Any]
