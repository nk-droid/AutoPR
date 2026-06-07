import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import redis as redis_sync
import redis.asyncio as redis
from pydantic import BaseModel, Field

logger = logging.getLogger("autopr.queue")

from core.orchestrator.models import RunType
from infra.github.models import IssueToPRContext, PRToMergeContext
from observability.tracing import inject_trace_context

WebhookJob = IssueToPRContext | PRToMergeContext

class WebhookQueueMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    enqueued_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attempts: int = 0
    kind: str = "webhook"
    run_type: RunType
    job_payload: dict[str, Any] = Field(default_factory=dict)
    resume_payload: dict[str, Any] = Field(default_factory=dict)
    last_error: str = ""
    trace_context: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_job(cls, job: WebhookJob) -> "WebhookQueueMessage":
        return cls(
            kind="webhook",
            run_type=job.run_type,
            job_payload=job.model_dump(mode="json"),
        )

    @classmethod
    def from_resume(
        cls,
        *,
        run_type: RunType,
        run_id: str,
        request_id: str,
        stage_index: int,
        context: dict[str, Any],
    ) -> "WebhookQueueMessage":
        return cls(
            kind="resume",
            run_type=run_type,
            resume_payload={
                "run_id": run_id,
                "request_id": request_id,
                "stage_index": int(stage_index),
                "context": context,
            },
        )

    def to_job(self) -> WebhookJob:
        if self.run_type == RunType.ISSUE_TO_PR:
            return IssueToPRContext.model_validate(self.job_payload)
        if self.run_type == RunType.PR_TO_MERGE:
            return PRToMergeContext.model_validate(self.job_payload)
        raise ValueError(f"Unsupported run_type: {self.run_type}")
    
class RedisWebhookQueue:
    def __init__(
        self,
        *,
        redis_client: redis.Redis,
        queue_key: str,
        processing_key: str,
        dlq_key: str,
        max_attempts: int,
    ) -> None:
        self._redis = redis_client
        self._queue_key = queue_key
        self._processing_key = processing_key
        self._dlq_key = dlq_key
        self._max_attempts = max_attempts

    @classmethod
    def from_env(cls) -> "RedisWebhookQueue":
        redis_url = os.getenv("AUTOPR_REDIS_URL", "redis://localhost:6379/0")
        queue_key = os.getenv("AUTOPR_WEBHOOK_QUEUE_KEY", "autopr:webhook:queue")
        processing_key = os.getenv("AUTOPR_WEBHOOK_PROCESSING_KEY", "autopr:webhook:processing")
        dlq_key = os.getenv("AUTOPR_WEBHOOK_DLQ_KEY", "autopr:webhook:dlq")
        max_attempts = int(os.getenv("AUTOPR_WEBHOOK_MAX_ATTEMPTS", "5"))
        client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=None,
            socket_connect_timeout=10,
            socket_keepalive=True,
            health_check_interval=30,
        )
        return cls(
            redis_client=client,
            queue_key=queue_key,
            processing_key=processing_key,
            dlq_key=dlq_key,
            max_attempts=max_attempts,
        )

    async def enqueue(self, job: WebhookJob) -> str:
        message = WebhookQueueMessage.from_job(job)
        message.trace_context = inject_trace_context()
        await self._redis.lpush(self._queue_key, message.model_dump_json())
        return message.message_id

    async def enqueue_resume(
        self,
        *,
        run_type: RunType,
        run_id: str,
        request_id: str,
        stage_index: int,
        context: dict[str, Any],
    ) -> str:
        message = WebhookQueueMessage.from_resume(
            run_type=run_type,
            run_id=run_id,
            request_id=request_id,
            stage_index=stage_index,
            context=context,
        )
        message.trace_context = inject_trace_context()
        await self._redis.lpush(self._queue_key, message.model_dump_json())
        return message.message_id

    async def reserve(self, timeout_sec: int = 5) -> tuple[WebhookQueueMessage, str] | None:
        timeout = int(timeout_sec) if timeout_sec is not None else 0

        try:
            raw = await self._redis.blmove(
                self._queue_key,
                self._processing_key,
                timeout=timeout,
                src="RIGHT",
                dest="LEFT",
            )
        except (redis.TimeoutError, redis.ConnectionError):
            return None

        if raw is None:
            return None

        return WebhookQueueMessage.model_validate_json(raw), raw

    async def ack(self, raw_message: str) -> None:
        await self._redis.lrem(self._processing_key, 1, raw_message)

    async def fail(self, message: WebhookQueueMessage, raw_message: str, error_text: str) -> None:
        next_message = message.model_copy(
            update={
                "attempts": message.attempts + 1,
                "last_error": error_text[:1000],
            }
        )
        pipe = self._redis.pipeline()
        pipe.lrem(self._processing_key, 1, raw_message)
        if next_message.attempts >= self._max_attempts:
            pipe.lpush(self._dlq_key, next_message.model_dump_json())
        else:
            pipe.lpush(self._queue_key, next_message.model_dump_json())
        await pipe.execute()

    async def close(self) -> None:
        await self._redis.aclose()


def start_queue_depth_sampler(interval_sec: float = 5.0) -> threading.Thread:
    from observability.metrics import QUEUE_DEPTH

    redis_url = os.getenv("AUTOPR_REDIS_URL", "redis://localhost:6379/0")
    keys = {
        "pending": os.getenv("AUTOPR_WEBHOOK_QUEUE_KEY", "autopr:webhook:queue"),
        "processing": os.getenv("AUTOPR_WEBHOOK_PROCESSING_KEY", "autopr:webhook:processing"),
        "dlq": os.getenv("AUTOPR_WEBHOOK_DLQ_KEY", "autopr:webhook:dlq"),
    }
    client = redis_sync.from_url(redis_url, decode_responses=True)

    def _loop() -> None:
        while True:
            try:
                pipe = client.pipeline()
                for key in keys.values():
                    pipe.llen(key)
                for (state, _), depth in zip(keys.items(), pipe.execute()):
                    QUEUE_DEPTH.labels(queue=state).set(depth)
            except Exception:
                logger.debug("queue depth sample failed", exc_info=True)
            time.sleep(interval_sec)

    thread = threading.Thread(target=_loop, name="queue-depth-sampler", daemon=True)
    thread.start()
    return thread