from typing import Any
from pydantic import BaseModel, Field

class CommandResult(BaseModel):
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float

class TestResult(BaseModel):
    success: bool
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
    raw_output: str = ""

class CoverageFile(BaseModel):
    path: str
    coverage_pct: float

class CoverageResult(BaseModel):
    success: bool
    coverage_pct: float
    threshold_passed: bool
    files: list[CoverageFile] = Field(default_factory=list)
    raw_output: str = ""

class LintIssue(BaseModel):
    file: str
    line: int
    code: str
    message: str

class LintResult(BaseModel):
    success: bool
    issues: list[LintIssue] = Field(default_factory=list)
    raw_output: str = ""

class SecurityIssue(BaseModel):
    severity: str
    file: str
    line: int
    issue: str

class SecurityResult(BaseModel):
    success: bool
    issues: list[SecurityIssue] = Field(default_factory=list)
    raw_output: str = ""

class QAResult(BaseModel):
    passed: bool
    tests_passed: bool
    coverage_passed: bool
    lint_passed: bool
    security_passed: bool
    summary: dict[str, Any]
