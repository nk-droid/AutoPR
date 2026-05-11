from enum import Enum

class PipelineStage(str, Enum):
    TRIAGE = "triage"
    PLAN = "plan"
    CODE = "code"
    QA = "qa"
    PUBLISH = "publish"
    PR_OPEN = "pr_open"
    REVIEW = "review"
    MERGE = "merge"

class RunState(str, Enum):
    RECEIVED = "RECEIVED"
    TRIAGED = "TRIAGED"
    PLANNED = "PLANNED"
    CODING = "CODING"
    QA_RUNNING = "QA_RUNNING"
    PR_OPENED = "PR_OPENED"
    REVIEW_PENDING = "REVIEW_PENDING"
    READY_TO_MERGE = "READY_TO_MERGE"
    MERGED = "MERGED"
