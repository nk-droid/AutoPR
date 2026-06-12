from enum import Enum


class PipelineStage(str, Enum):
    TRIAGE = "triage"
    PLAN = "plan"
    PREPARE = "prepare"
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
    PUBLISHED = "PUBLISHED"
    PR_OPENED = "PR_OPENED"
    REVIEW_PENDING = "REVIEW_PENDING"
    READY_TO_MERGE = "READY_TO_MERGE"
    MERGED = "MERGED"
    BLOCKED = "BLOCKED"


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GitHubWebhookEventType(str, Enum):
    ISSUES = "issues"
    PULL_REQUEST_REVIEW = "pull_request_review"


class GitHubPullRequestReviewAction(str, Enum):
    SUBMITTED = "submitted"
    EDITED = "edited"
    DISMISSED = "dismissed"


class GitHubPullRequestState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class GitHubReviewState(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"
    DISMISSED = "dismissed"
    PENDING = "pending"


class GitHubMergeableState(str, Enum):
    CLEAN = "clean"
    HAS_HOOKS = "has_hooks"
    UNSTABLE = "unstable"
    DIRTY = "dirty"
    BLOCKED = "blocked"
    BEHIND = "behind"
    DRAFT = "draft"


class GitHubIssueState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class GitHubIssueSort(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    COMMENTS = "comments"


class GitHubSortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class GitHubIssuePickStrategy(str, Enum):
    OLDEST_OPEN = "oldest_open"
    NEWEST_OPEN = "newest_open"
    LEAST_COMMENTS = "least_comments"
    MOST_COMMENTS = "most_comments"


class GitHubPathSegment(str, Enum):
    ISSUES = "issues"


class WebhookResultType(str, Enum):
    ACCEPTED = "accepted"
    IGNORED = "ignored"
