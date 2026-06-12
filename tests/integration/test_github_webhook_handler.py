import importlib
import hashlib
import hmac
import json
import sys
import types
import uuid
from datetime import datetime
from datetime import timezone

import pytest

from core.contracts.enums import RunState
from core.orchestrator.models import RunType
from infra.github.models import GitHubRepo
from infra.github.models import GitHubWebhookEventMetadata
from infra.github.models import IssueToPRContext
from infra.github.models import PRToMergeContext
from infra.github.models import WebhookDispatchResult


def _load_webhook_handler_module():
    fake_coordinator_module = types.ModuleType("core.orchestrator.coordinator")
    fake_resume_module = types.ModuleType("core.orchestrator.resume")

    class _ImportSafeCoordinator:
        def __init__(self, run):
            self.run = run

        def run_issue_to_pr(self, context):
            return self.run

        def run_pr_to_merge(self, context):
            return self.run

    fake_coordinator_module.Coordinator = _ImportSafeCoordinator
    fake_resume_module.resume_after_approval = lambda **_kwargs: None
    sys.modules["core.orchestrator.coordinator"] = fake_coordinator_module
    sys.modules["core.orchestrator.resume"] = fake_resume_module
    sys.modules.pop("infra.github.webhook_handler", None)
    return importlib.import_module("infra.github.webhook_handler")


webhook_handler = _load_webhook_handler_module()


class _CommentClient:
    def list_issue_comments(self, repo: str, issue_number: int):
        return []


def _issue_payload_dict(action: str = "opened") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "action": action,
        "issue": {
            "number": 7,
            "url": "https://github.com/acme/repo/issues/7",
            "title": "Fix bug",
            "body": "details",
            "created_at": now,
            "updated_at": now,
        },
        "repository": {
            "full_name": "acme/repo",
            "url": "https://github.com/acme/repo",
            "default_branch": "main",
        },
    }


def test_verify_signature_with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"x":1}'
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "shh")
    digest = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    signature = f"sha256={digest}"
    webhook_handler._verify_signature(body, signature)
    with pytest.raises(PermissionError, match="Missing X-Hub-Signature-256"):
        webhook_handler._verify_signature(body, None)
    with pytest.raises(PermissionError, match="Invalid X-Hub-Signature-256 format"):
        webhook_handler._verify_signature(body, "bad")
    with pytest.raises(PermissionError, match="verification failed"):
        webhook_handler._verify_signature(body, "sha256=deadbeef")


def test_handle_github_webhook_issues_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setattr("infra.github.models.GitHubClient", lambda: _CommentClient())
    body = json.dumps(_issue_payload_dict(action="opened")).encode("utf-8")
    result = webhook_handler.handle_github_webhook(
        event_type="issues",
        delivery_id="d-1",
        body=body,
        signature_256=None,
    )
    assert result.accepted is True
    assert result.ignored_reason == ""
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert isinstance(job, IssueToPRContext)
    assert job.issue_number == 7
    assert job.head_branch == "autopr/issue-7"


def test_handle_github_webhook_filtered_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setattr("infra.github.models.GitHubClient", lambda: _CommentClient())
    body = json.dumps(_issue_payload_dict(action="edited")).encode("utf-8")
    result = webhook_handler.handle_github_webhook(
        event_type="issues",
        delivery_id="d-2",
        body=body,
        signature_256=None,
    )
    assert result.accepted is True
    assert result.jobs == []
    assert result.ignored_reason == "event_not_mapped_or_filtered"


def test_handle_github_webhook_missing_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    with pytest.raises(ValueError, match="Missing X-GitHub-Event"):
        webhook_handler.handle_github_webhook(
            event_type="",
            delivery_id="d-3",
            body=b"{}",
            signature_256=None,
        )
    with pytest.raises(ValueError, match="Missing X-GitHub-Delivery"):
        webhook_handler.handle_github_webhook(
            event_type="issues",
            delivery_id="",
            body=b"{}",
            signature_256=None,
        )


class _FakeCoordinator:
    def __init__(self, run):
        self.run = run

    def run_issue_to_pr(self, context):
        self.run.state = RunState.PR_OPENED.value
        return self.run

    def run_pr_to_merge(self, context):
        self.run.state = RunState.MERGED.value
        return self.run


def _make_issue_job() -> IssueToPRContext:
    repo = GitHubRepo(
        full_name="acme/repo",
        url="https://github.com/acme/repo",
        default_branch="main",
    )
    metadata = GitHubWebhookEventMetadata(
        event_type="issues",
        delivery_id="d-4",
        action="opened",
    )
    return IssueToPRContext(
        run_id=uuid.uuid4(),
        run_type=RunType.ISSUE_TO_PR,
        metadata=metadata,
        repository=repo,
        issue_number=8,
        head_branch="autopr/issue-8",
        base_branch="main",
        execute_remote_actions=False,
    )


def _make_merge_job() -> PRToMergeContext:
    repo = GitHubRepo(
        full_name="acme/repo",
        url="https://github.com/acme/repo",
        default_branch="main",
    )
    metadata = GitHubWebhookEventMetadata(
        event_type="pull_request_review",
        delivery_id="d-5",
        action="submitted",
    )
    return PRToMergeContext(
        run_id=uuid.uuid4(),
        run_type=RunType.PR_TO_MERGE,
        metadata=metadata,
        repository=repo,
        pull_request_number=33,
        review_approved=True,
        execute_remote_actions=False,
    )


def test_dispatch_webhook_job_issue_and_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook_handler, "Coordinator", _FakeCoordinator)
    issue_result = webhook_handler.dispatch_webhook_job(_make_issue_job())
    assert isinstance(issue_result, WebhookDispatchResult)
    assert issue_result.accepted is True
    assert issue_result.run_type == RunType.ISSUE_TO_PR.value
    assert issue_result.state == RunState.PR_OPENED.value
    merge_result = webhook_handler.dispatch_webhook_job(_make_merge_job())
    assert merge_result.run_type == RunType.PR_TO_MERGE.value
    assert merge_result.state == RunState.MERGED.value
