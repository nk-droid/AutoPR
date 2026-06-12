import asyncio
import importlib
import json
import sys
import types
from datetime import datetime, timezone

import pytest

from core.contracts.enums import RunState
from core.orchestrator.models import RunType
from infra.redis.webhook_queue import RedisWebhookQueue

pytestmark = pytest.mark.e2e


def _load_webhook_handler_module():
    fake_coordinator_module = types.ModuleType("core.orchestrator.coordinator")
    fake_resume_module = types.ModuleType("core.orchestrator.resume")

    class _ImportSafeCoordinator:
        def __init__(self, run):
            self.run = run

        def run_issue_to_pr(self, context):
            del context
            return self.run

        def run_pr_to_merge(self, context):
            del context
            return self.run

    fake_coordinator_module.Coordinator = _ImportSafeCoordinator
    fake_resume_module.resume_after_approval = lambda **_kwargs: None
    sys.modules["core.orchestrator.coordinator"] = fake_coordinator_module
    sys.modules["core.orchestrator.resume"] = fake_resume_module
    sys.modules.pop("infra.github.webhook_handler", None)
    return importlib.import_module("infra.github.webhook_handler")


class _CommentClient:
    def list_issue_comments(self, repo: str, issue_number: int):
        del repo
        del issue_number
        return []


class _FlowCoordinator:
    def __init__(self, run):
        self.run = run

    def run_issue_to_pr(self, context):
        self.run.state = RunState.PR_OPENED.value
        self.run.repository = context.repository
        self.run.issue_number = context.issue_number
        return self.run

    def run_pr_to_merge(self, context):
        self.run.state = RunState.MERGED.value
        self.run.repository = context.repository
        self.run.pull_request_number = context.pull_request_number
        return self.run


class _InMemoryRedis:
    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}
        self.closed = False

    def _list(self, key: str) -> list[str]:
        return self._lists.setdefault(key, [])

    async def lpush(self, key: str, value: str):
        bucket = self._list(key)
        bucket.insert(0, value)
        return len(bucket)

    async def blmove(
        self,
        source: str,
        destination: str,
        *,
        timeout: int,
        src: str,
        dest: str,
    ):
        del timeout
        if src != "RIGHT" or dest != "LEFT":
            raise AssertionError("unexpected source/destination direction")
        source_bucket = self._list(source)
        if not source_bucket:
            return None
        value = source_bucket.pop()
        self._list(destination).insert(0, value)
        return value

    async def lrem(self, key: str, count: int, value: str):
        if count < 0:
            raise AssertionError("negative lrem count is unsupported in test double")
        bucket = self._list(key)
        removed = 0
        index = 0
        while index < len(bucket) and removed < count:
            if bucket[index] == value:
                bucket.pop(index)
                removed += 1
                continue
            index += 1
        return removed

    async def aclose(self):
        self.closed = True


def _issue_payload(action: str = "opened") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "action": action,
        "issue": {
            "number": 71,
            "url": "https://github.com/acme/repo/issues/71",
            "title": "Webhook driven issue flow",
            "body": "trigger pipeline",
            "created_at": now,
            "updated_at": now,
        },
        "repository": {
            "full_name": "acme/repo",
            "url": "https://github.com/acme/repo",
            "default_branch": "main",
        },
    }


def _review_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "action": "submitted",
        "review": {
            "state": "approved",
        },
        "pull_request": {
            "number": 45,
            "url": "https://github.com/acme/repo/pull/45",
            "title": "Webhook merge flow",
            "body": "ready",
            "created_at": now,
            "updated_at": now,
            "state": "open",
        },
        "repository": {
            "full_name": "acme/repo",
            "url": "https://github.com/acme/repo",
            "default_branch": "main",
        },
    }


def test_e2e_issue_webhook_to_queue_to_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    webhook_handler = _load_webhook_handler_module()
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setattr("infra.github.models.GitHubClient", lambda: _CommentClient())
    monkeypatch.setattr(webhook_handler, "Coordinator", _FlowCoordinator)

    async def run_flow() -> None:
        payload = json.dumps(_issue_payload()).encode("utf-8")
        handled = webhook_handler.handle_github_webhook(
            event_type="issues",
            delivery_id="delivery-e2e-issue",
            body=payload,
            signature_256=None,
        )
        assert handled.accepted is True
        assert handled.ignored_reason == ""
        assert len(handled.jobs) == 1

        redis_client = _InMemoryRedis()
        queue = RedisWebhookQueue(
            redis_client=redis_client,
            queue_key="q",
            processing_key="p",
            dlq_key="d",
            max_attempts=2,
        )
        for job in handled.jobs:
            await queue.enqueue(job)

        reserved = await queue.reserve(timeout_sec=1)
        assert reserved is not None
        message, raw = reserved
        dispatch_result = webhook_handler.dispatch_webhook_job(message.to_job())
        await queue.ack(raw)
        await queue.close()

        assert dispatch_result.accepted is True
        assert dispatch_result.run_type == RunType.ISSUE_TO_PR.value
        assert dispatch_result.state == RunState.PR_OPENED.value
        assert redis_client._list("q") == []
        assert redis_client._list("p") == []
        assert redis_client.closed is True

    asyncio.run(run_flow())


def test_e2e_pr_review_webhook_to_queue_to_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    webhook_handler = _load_webhook_handler_module()
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setenv("AUTOPR_WEBHOOK_MERGE_ON_APPROVAL", "true")
    monkeypatch.setattr(webhook_handler, "Coordinator", _FlowCoordinator)

    async def run_flow() -> None:
        payload = json.dumps(_review_payload()).encode("utf-8")
        handled = webhook_handler.handle_github_webhook(
            event_type="pull_request_review",
            delivery_id="delivery-e2e-review",
            body=payload,
            signature_256=None,
        )
        assert handled.accepted is True
        assert handled.ignored_reason == ""
        assert len(handled.jobs) == 1

        redis_client = _InMemoryRedis()
        queue = RedisWebhookQueue(
            redis_client=redis_client,
            queue_key="q",
            processing_key="p",
            dlq_key="d",
            max_attempts=2,
        )
        for job in handled.jobs:
            await queue.enqueue(job)

        reserved = await queue.reserve(timeout_sec=1)
        assert reserved is not None
        message, raw = reserved
        dispatch_result = webhook_handler.dispatch_webhook_job(message.to_job())
        await queue.ack(raw)
        await queue.close()

        assert dispatch_result.accepted is True
        assert dispatch_result.run_type == RunType.PR_TO_MERGE.value
        assert dispatch_result.state == RunState.MERGED.value
        assert redis_client._list("q") == []
        assert redis_client._list("p") == []
        assert redis_client.closed is True

    asyncio.run(run_flow())
