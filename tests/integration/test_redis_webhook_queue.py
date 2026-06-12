import asyncio
import uuid

import pytest

from core.orchestrator.models import RunType
from infra.github.models import GitHubRepo
from infra.github.models import GitHubWebhookEventMetadata
from infra.github.models import IssueToPRContext
from infra.github.models import PRToMergeContext
from infra.redis.webhook_queue import RedisWebhookQueue
from infra.redis.webhook_queue import WebhookQueueMessage


def _repo() -> GitHubRepo:
    return GitHubRepo(
        full_name="acme/repo",
        url="https://github.com/acme/repo",
        default_branch="main",
    )


def _meta(event_type: str, action: str) -> GitHubWebhookEventMetadata:
    return GitHubWebhookEventMetadata(
        event_type=event_type,
        delivery_id="d-redis",
        action=action,
    )


def _issue_job() -> IssueToPRContext:
    return IssueToPRContext(
        run_id=uuid.uuid4(),
        run_type=RunType.ISSUE_TO_PR,
        metadata=_meta("issues", "opened"),
        repository=_repo(),
        issue_number=9,
        head_branch="autopr/issue-9",
        base_branch="main",
        execute_remote_actions=False,
    )


def _pr_job() -> PRToMergeContext:
    return PRToMergeContext(
        run_id=uuid.uuid4(),
        run_type=RunType.PR_TO_MERGE,
        metadata=_meta("pull_request_review", "submitted"),
        repository=_repo(),
        pull_request_number=22,
        review_approved=True,
        execute_remote_actions=False,
    )


class _FakePipe:
    def __init__(self) -> None:
        self.ops: list[tuple] = []

    def lrem(self, key: str, count: int, value: str):
        self.ops.append(("lrem", key, count, value))
        return self

    def lpush(self, key: str, value: str):
        self.ops.append(("lpush", key, value))
        return self

    async def execute(self):
        self.ops.append(("execute",))
        return True


class _FakeRedis:
    def __init__(self) -> None:
        self.lpush_calls: list[tuple[str, str]] = []
        self.lrem_calls: list[tuple[str, int, str]] = []
        self.blmove_calls: list[tuple[tuple, dict]] = []
        self.blmove_result = None
        self.pipe = _FakePipe()
        self.closed = False

    async def lpush(self, key: str, value: str):
        self.lpush_calls.append((key, value))
        return 1

    async def blmove(self, *args, **kwargs):
        self.blmove_calls.append((args, kwargs))
        return self.blmove_result

    async def lrem(self, key: str, count: int, value: str):
        self.lrem_calls.append((key, count, value))
        return 1

    def pipeline(self):
        return self.pipe

    async def aclose(self):
        self.closed = True


def test_webhook_queue_message_roundtrip_issue_and_pr() -> None:
    issue_message = WebhookQueueMessage.from_job(_issue_job())
    assert issue_message.run_type == RunType.ISSUE_TO_PR
    issue_job = issue_message.to_job()
    assert isinstance(issue_job, IssueToPRContext)
    assert issue_job.issue_number == 9
    pr_message = WebhookQueueMessage.from_job(_pr_job())
    assert pr_message.run_type == RunType.PR_TO_MERGE
    pr_job = pr_message.to_job()
    assert isinstance(pr_job, PRToMergeContext)
    assert pr_job.pull_request_number == 22
    bad = WebhookQueueMessage.model_construct(
        message_id="x",
        run_type="UNKNOWN",
        job_payload={},
        enqueued_at="2024-01-01T00:00:00+00:00",
        attempts=0,
        last_error="",
    )
    with pytest.raises(ValueError, match="Unsupported run_type"):
        bad.to_job()


def test_redis_webhook_queue_enqueue_reserve_ack_fail_close() -> None:
    async def run_test() -> None:
        fake = _FakeRedis()
        queue = RedisWebhookQueue(
            redis_client=fake,
            queue_key="queue",
            processing_key="processing",
            dlq_key="dlq",
            max_attempts=2,
        )
        message_id = await queue.enqueue(_issue_job())
        assert isinstance(message_id, str)
        assert fake.lpush_calls[0][0] == "queue"
        raw = fake.lpush_calls[0][1]
        fake.blmove_result = raw
        reserved = await queue.reserve(timeout_sec=2)
        assert reserved is not None
        message, raw_message = reserved
        assert raw_message == raw
        await queue.ack(raw_message)
        assert fake.lrem_calls == [("processing", 1, raw_message)]
        await queue.fail(message, raw_message, "first failure")
        assert (
            "lpush",
            "queue",
        ) == fake.pipe.ops[1][:2]
        second = message.model_copy(update={"attempts": 1})
        await queue.fail(second, raw_message, "second failure")
        assert fake.pipe.ops[-2][1] == "dlq"
        await queue.close()
        assert fake.closed is True

    asyncio.run(run_test())


def test_redis_webhook_queue_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = object()
    captured: dict[str, object] = {}

    def _fake_from_url(url: str, **kwargs):
        captured["url"] = url
        captured["encoding"] = kwargs.get("encoding")
        captured["decode_responses"] = kwargs.get("decode_responses")
        return fake_client

    monkeypatch.setenv("AUTOPR_REDIS_URL", "redis://cache:6379/8")
    monkeypatch.setenv("AUTOPR_WEBHOOK_QUEUE_KEY", "q")
    monkeypatch.setenv("AUTOPR_WEBHOOK_PROCESSING_KEY", "p")
    monkeypatch.setenv("AUTOPR_WEBHOOK_DLQ_KEY", "d")
    monkeypatch.setenv("AUTOPR_WEBHOOK_MAX_ATTEMPTS", "7")
    monkeypatch.setattr("infra.redis.webhook_queue.redis.from_url", _fake_from_url)

    queue = RedisWebhookQueue.from_env()

    assert captured == {
        "url": "redis://cache:6379/8",
        "encoding": "utf-8",
        "decode_responses": True,
    }
    assert queue._redis is fake_client
    assert queue._queue_key == "q"
    assert queue._processing_key == "p"
    assert queue._dlq_key == "d"
    assert queue._max_attempts == 7


def test_redis_webhook_queue_reserve_none_with_timeout_zero() -> None:
    async def run_test() -> None:
        fake = _FakeRedis()
        queue = RedisWebhookQueue(
            redis_client=fake,
            queue_key="queue",
            processing_key="processing",
            dlq_key="dlq",
            max_attempts=3,
        )

        reserved = await queue.reserve(timeout_sec=None)

        assert reserved is None
        assert fake.blmove_calls[0][1]["timeout"] == 0
        assert fake.blmove_calls[0][1]["src"] == "RIGHT"
        assert fake.blmove_calls[0][1]["dest"] == "LEFT"

    asyncio.run(run_test())


def test_redis_webhook_queue_fail_truncates_error_before_requeue() -> None:
    async def run_test() -> None:
        fake = _FakeRedis()
        queue = RedisWebhookQueue(
            redis_client=fake,
            queue_key="queue",
            processing_key="processing",
            dlq_key="dlq",
            max_attempts=5,
        )
        message = WebhookQueueMessage.from_job(_issue_job())
        raw_message = message.model_dump_json()

        await queue.fail(message, raw_message, "x" * 1500)

        assert fake.pipe.ops[0] == ("lrem", "processing", 1, raw_message)
        assert fake.pipe.ops[1][0] == "lpush"
        requeued_payload = WebhookQueueMessage.model_validate_json(fake.pipe.ops[1][2])
        assert requeued_payload.attempts == 1
        assert len(requeued_payload.last_error) == 1000
        assert fake.pipe.ops[2] == ("execute",)

    asyncio.run(run_test())


def test_webhook_queue_message_resume_round_trip() -> None:
    message = WebhookQueueMessage.from_resume(
        run_type=RunType.ISSUE_TO_PR,
        run_id="run-1",
        request_id="rq-1",
        stage_index=4,
        context={"repository": "acme/repo", "issue_number": 9},
    )
    restored = WebhookQueueMessage.model_validate_json(message.model_dump_json())
    assert restored.kind == "resume"
    assert restored.run_type == RunType.ISSUE_TO_PR
    assert restored.resume_payload == {
        "run_id": "run-1",
        "request_id": "rq-1",
        "stage_index": 4,
        "context": {"repository": "acme/repo", "issue_number": 9},
    }


def test_webhook_queue_message_defaults_to_webhook_kind() -> None:
    # Messages enqueued before the kind/resume fields existed must still parse.
    legacy = (
        '{"message_id":"x","enqueued_at":"2024-01-01T00:00:00+00:00","attempts":0,'
        '"run_type":"ISSUE_TO_PR","job_payload":{"a":1},"last_error":"","trace_context":{}}'
    )
    message = WebhookQueueMessage.model_validate_json(legacy)
    assert message.kind == "webhook"
    assert message.resume_payload == {}


def test_redis_webhook_queue_enqueue_resume_pushes_resume_message() -> None:
    async def run_test() -> None:
        fake = _FakeRedis()
        queue = RedisWebhookQueue(
            redis_client=fake,
            queue_key="queue",
            processing_key="processing",
            dlq_key="dlq",
            max_attempts=2,
        )
        message_id = await queue.enqueue_resume(
            run_type=RunType.PR_TO_MERGE,
            run_id="run-9",
            request_id="rq-9",
            stage_index=0,
            context={"pull_request_number": 22},
        )
        assert isinstance(message_id, str)
        assert fake.lpush_calls[0][0] == "queue"
        pushed = WebhookQueueMessage.model_validate_json(fake.lpush_calls[0][1])
        assert pushed.kind == "resume"
        assert pushed.run_type == RunType.PR_TO_MERGE
        assert pushed.resume_payload["request_id"] == "rq-9"
        assert pushed.resume_payload["run_id"] == "run-9"

    asyncio.run(run_test())
