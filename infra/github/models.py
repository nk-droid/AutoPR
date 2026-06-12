import uuid
from datetime import datetime
from typing import Union
from pydantic import BaseModel, Field, model_validator
from core.contracts.enums import GitHubPullRequestState, GitHubReviewState, GitHubWebhookEventType
from core.orchestrator.models import RunType
from infra.github.client import GitHubClient


class GitHubWebhookEventMetadata(BaseModel):
    event_type: GitHubWebhookEventType
    delivery_id: str
    action: str
    source: str = "github_webhook"


class GitHubRepo(BaseModel):
    full_name: str
    url: str
    default_branch: str


class RunContext(BaseModel):
    run_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    run_type: RunType
    metadata: GitHubWebhookEventMetadata
    repository: GitHubRepo


class IssueToPRContext(RunContext):
    issue_number: int
    head_branch: str
    base_branch: str
    execute_remote_actions: bool = False


class PRToMergeContext(RunContext):
    pull_request_number: int
    review_approved: bool
    execute_remote_actions: bool = False


class GitHubComment(BaseModel):
    url: str
    body: str | None = None
    created_at: datetime
    updated_at: datetime


class GitHubIssue(BaseModel):
    number: int
    url: str
    title: str
    body: str | None = None
    comment_list: list[GitHubComment] = Field(default=[])
    created_at: datetime
    updated_at: datetime


class IssuePayload(BaseModel):
    action: str
    issue: GitHubIssue
    repository: GitHubRepo

    @model_validator(mode="after")
    def populate_comments(self) -> "IssuePayload":
        """Pull issue comments and add to GitHubIssue model"""
        github_client = GitHubClient()
        comments = github_client.list_issue_comments(
            repo=self.repository.full_name, issue_number=self.issue.number
        )
        self.issue.comment_list = [GitHubComment(**comment) for comment in comments]
        return self


class GitHubPullRequest(BaseModel):
    number: int
    url: str
    title: str
    body: str | None = None
    created_at: datetime
    updated_at: datetime
    state: GitHubPullRequestState


class GitHubReview(BaseModel):
    state: GitHubReviewState


class PRReviewPayload(BaseModel):
    action: str
    review: GitHubReview
    pull_request: GitHubPullRequest
    repository: GitHubRepo


class WebhookHandleResult(BaseModel):
    accepted: bool
    duplicate: bool
    ignored_reason: str
    jobs: list[Union[IssueToPRContext, PRToMergeContext]]


class WebhookDispatchResult(BaseModel):
    accepted: bool
    run_id: str
    state: str
    run_type: str
