import hashlib
import hmac
import json
import os
import re
from typing import Any, Union
from core.contracts.enums import GitHubPullRequestReviewAction, GitHubPullRequestState, GitHubReviewState, GitHubWebhookEventType, RunState
from core.contracts.run_context import IssueToPRContext as PipelineIssueToPRContext
from core.contracts.run_context import PRToMergeContext as PipelinePRToMergeContext
from core.orchestrator.coordinator import Coordinator
from core.orchestrator.models import IssueActions, RunModel, RunType
from core.orchestrator.resume import resume_after_approval
from infra.github.models import GitHubRepo, GitHubWebhookEventMetadata, IssuePayload, IssueToPRContext, PRReviewPayload, PRToMergeContext, WebhookDispatchResult, WebhookHandleResult

ISSUE_RUN_COMMAND = re.compile(r"(?im)^/autopr\s+run\b")
PR_MERGE_COMMAND = re.compile(r"(?im)^/autopr\s+merge\b")

def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

def _verify_signature(body: bytes, signature_256: str | None) -> None:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return

    if not signature_256:
        raise PermissionError("Missing X-Hub-Signature-256 header")

    prefix = "sha256="
    if not signature_256.startswith(prefix):
        raise PermissionError("Invalid X-Hub-Signature-256 format")

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = f"{prefix}{digest}"
    if not hmac.compare_digest(signature_256, expected):
        raise PermissionError("Webhook signature verification failed")

def _base_metadata(event_type: GitHubWebhookEventType, delivery_id: str, action: str) -> GitHubWebhookEventMetadata:
    return GitHubWebhookEventMetadata(
        event_type=event_type,
        delivery_id=delivery_id,
        action=action
    )

def _build_issue_to_pr_job(
    *,
    repository: GitHubRepo,
    issue_number: int,
    event_type: GitHubWebhookEventType,
    delivery_id: str,
    action: str,
    base_branch: str,
) -> IssueToPRContext:
    """
    Build an issue workflow job from a supported GitHub issue event.

    Args:
        repository: Repository metadata from the webhook payload.
        issue_number: Issue number that should drive the workflow.
        event_type: Normalized webhook event type.
        delivery_id: GitHub delivery identifier for traceability.
        action: GitHub event action that triggered the job.
        base_branch: Repository default branch for the generated PR.

    Returns:
        Webhook job context for issue-to-PR processing.
    """

    metadata = _base_metadata(event_type, delivery_id, action)
    return IssueToPRContext(
        metadata=metadata,
        run_type=RunType.ISSUE_TO_PR,
        repository=repository,
        issue_number=issue_number,
        head_branch=f"autopr/issue-{issue_number}",
        base_branch=base_branch,
        execute_remote_actions=_env_flag("AUTOPR_EXECUTE_REMOTE_ACTIONS", False),
    )

def _build_pr_to_merge_job(
    *,
    repository: GitHubRepo,
    pull_request_number: int,
    event_type: GitHubWebhookEventType,
    delivery_id: str,
    action: str,
    review_approved: bool,
) -> PRToMergeContext:
    """
    Build a merge workflow job from a supported pull-request review event.

    Args:
        repository: Repository metadata from the webhook payload.
        pull_request_number: Pull request number that may be merged.
        event_type: Normalized webhook event type.
        delivery_id: GitHub delivery identifier for traceability.
        action: GitHub event action that triggered the job.
        review_approved: Whether the review event approved the pull request.

    Returns:
        Webhook job context for PR-to-merge processing.
    """

    metadata = _base_metadata(event_type, delivery_id, action)
    return PRToMergeContext(
        metadata=metadata,
        run_type=RunType.PR_TO_MERGE,
        repository=repository,
        pull_request_number=pull_request_number,
        review_approved=review_approved,
        execute_remote_actions=_env_flag("AUTOPR_EXECUTE_REMOTE_ACTIONS", False),
    )

def _jobs_for_issues_event(
    event_type: GitHubWebhookEventType,
    delivery_id: str,
    payload: IssuePayload,
) -> list[IssueToPRContext]:
    """
    Convert eligible issue lifecycle events into issue-to-PR jobs.

    Args:
        event_type: Normalized webhook event type.
        delivery_id: GitHub delivery identifier for traceability.
        payload: Validated GitHub issue webhook payload.

    Returns:
        Zero or one jobs after feature flags and action filters apply.
    """

    if not _env_flag("AUTOPR_WEBHOOK_RUN_ON_ISSUES", True):
        return []

    # Only trigger issue workflow for lifecycle events we explicitly support.
    if payload.action not in {IssueActions.OPENED.value, IssueActions.REOPENED.value}:
        return []

    issue = payload.issue
    repo = payload.repository
    if not repo.full_name or issue.number is None:
        return []

    return [
        _build_issue_to_pr_job(
            repository=repo,
            issue_number=issue.number,
            event_type=event_type,
            delivery_id=delivery_id,
            action=payload.action,
            base_branch=repo.default_branch,
        )
    ]

def _jobs_for_pr_review_event(
    event_type: GitHubWebhookEventType,
    delivery_id: str,
    payload: PRReviewPayload,
) -> list[PRToMergeContext]:
    """
    Convert eligible approved-review events into PR-to-merge jobs.

    Args:
        event_type: Normalized webhook event type.
        delivery_id: GitHub delivery identifier for traceability.
        payload: Validated GitHub pull request review payload.

    Returns:
        Zero or one jobs after feature flags and approval filters apply.
    """

    if not _env_flag("AUTOPR_WEBHOOK_MERGE_ON_APPROVAL", False):
        return []

    # Merge workflow is opt-in and only starts from submitted approvals.
    if payload.action != GitHubPullRequestReviewAction.SUBMITTED.value:
        return []

    pr = payload.pull_request
    if pr.state != GitHubPullRequestState.OPEN:
        return []

    review = payload.review
    if review.state != GitHubReviewState.APPROVED:
        return []

    repo = payload.repository
    if not repo.full_name or pr.number is None:
        return []

    return [
        _build_pr_to_merge_job(
            repository=repo,
            pull_request_number=pr.number,
            event_type=event_type,
            delivery_id=delivery_id,
            action=payload.action,
            review_approved=True,
        )
    ]

def _build_jobs(event_type: str, delivery_id: str, payload: dict[str, Any]) -> Union[list[IssueToPRContext], list[PRToMergeContext]]:
    """
    Route a GitHub webhook payload to the pipeline jobs it should create.

    Args:
        event_type: GitHub event header value.
        delivery_id: GitHub delivery identifier for traceability.
        payload: Parsed webhook JSON body.

    Returns:
        Workflow jobs created from supported and enabled events.
    """

    # Map webhook event type to pipeline job(s); unsupported events are ignored.
    if event_type == GitHubWebhookEventType.ISSUES.value:
        return _jobs_for_issues_event(GitHubWebhookEventType.ISSUES, delivery_id, IssuePayload(**payload))

    if event_type == GitHubWebhookEventType.PULL_REQUEST_REVIEW.value:
        return _jobs_for_pr_review_event(GitHubWebhookEventType.PULL_REQUEST_REVIEW, delivery_id, PRReviewPayload(**payload))

    return []

def handle_github_webhook(
    *,
    event_type: str,
    delivery_id: str,
    body: bytes,
    signature_256: str | None,
) -> WebhookHandleResult:
    """
    Validate and map a GitHub webhook into queued pipeline jobs.

    Args:
        event_type: GitHub event header value.
        delivery_id: GitHub delivery identifier.
        body: Raw webhook request body used for signature validation.
        signature_256: Optional GitHub SHA-256 signature header.

    Returns:
        Webhook handling result containing jobs or an ignored reason.
    """

    normalized_event = event_type or ""
    if not normalized_event:
        raise ValueError("Missing X-GitHub-Event header")

    normalized_delivery = delivery_id or ""
    if not normalized_delivery:
        raise ValueError("Missing X-GitHub-Delivery header")

    _verify_signature(body, signature_256)

    payload = json.loads(body)
    jobs = _build_jobs(normalized_event, normalized_delivery, payload)
    ignored_reason = "" if jobs else "event_not_mapped_or_filtered"
    return WebhookHandleResult(
        accepted=True,
        duplicate=False,
        ignored_reason=ignored_reason,
        jobs=jobs,
    )

def dispatch_webhook_job(job: Union[IssueToPRContext, PRToMergeContext]) -> WebhookDispatchResult:
    """
    Execute a queued webhook job through the appropriate coordinator workflow.

    Args:
        job: Validated issue-to-PR or PR-to-merge webhook job.

    Returns:
        Dispatch result containing the run id, state, and run type.
    """

    repository = job.repository.full_name
    issue_number = job.issue_number if hasattr(job, "issue_number") else None
    pull_request_number = job.pull_request_number if hasattr(job, "pull_request_number") else None

    run = RunModel(
        state=RunState.RECEIVED.value,
        run_type=job.run_type,
        repository=repository,
        issue_number=issue_number,
        pull_request_number=pull_request_number,
        metadata=job.metadata.model_dump(mode="json"),
    )

    coordinator = Coordinator(run)
    if job.run_type == RunType.ISSUE_TO_PR:
        issue_job = job
        final_run = coordinator.run_issue_to_pr(
            PipelineIssueToPRContext(
                repository=repository,
                issue_number=issue_job.issue_number,
                execute_remote_actions=issue_job.execute_remote_actions,
                head_branch=issue_job.head_branch,
                base_branch=issue_job.base_branch,
                metadata=issue_job.metadata.model_dump(mode="json"),
            )
        )
    else:
        merge_job = job
        final_run = coordinator.run_pr_to_merge(
            PipelinePRToMergeContext(
                repository=repository,
                pull_request_number=merge_job.pull_request_number,
                review_approved=merge_job.review_approved,
                execute_remote_actions=merge_job.execute_remote_actions,
                metadata=merge_job.metadata.model_dump(mode="json"),
            )
        )

    return WebhookDispatchResult(
        accepted=True,
        run_id=str(final_run.run_id),
        state=final_run.state,
        run_type=job.run_type.value,
    )

def dispatch_resume_job(resume_payload: dict[str, Any]) -> WebhookDispatchResult:
    """
    Execute a queued review-resume message after a human approval.

    Args:
        resume_payload: Stored resume fields from the queue message.

    Returns:
        Dispatch result containing the resumed run id, state, and run type.
    """

    final_run = resume_after_approval(
        request_id=str(resume_payload.get("request_id", "")),
        run_id=str(resume_payload.get("run_id", "")),
        stage_index=int(resume_payload.get("stage_index", 0)),
        context=dict(resume_payload.get("context", {})),
    )

    return WebhookDispatchResult(
        accepted=True,
        run_id=str(final_run.run_id),
        state=final_run.state,
        run_type=final_run.run_type.value,
    )

if __name__ == "__main__":
    with open("payload.json", "r", encoding="utf-8") as f:
        payload = json.load(f)
    jobs = _build_jobs("issues", "1", payload)
    for job in jobs:
        dispatch_webhook_job(job)
