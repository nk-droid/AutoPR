import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from pydantic import BaseModel, Field

from core.orchestrator.models import RunType
from infra.github.models import IssueToPRContext, PRToMergeContext

WebhookJob = IssueToPRContext | PRToMergeContext

class WebhookQueueMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    enqueued_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attempts: int = 0
    run_type: RunType
    job_payload: dict[str, Any]
    last_error: str = ""

    @classmethod
    def from_job(cls, job: WebhookJob) -> "WebhookQueueMessage":
        return cls(
            run_type=job.run_type,
            job_payload=job.model_dump(mode="json"),
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
        client = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        return cls(
            redis_client=client,
            queue_key=queue_key,
            processing_key=processing_key,
            dlq_key=dlq_key,
            max_attempts=max_attempts,
        )

    async def enqueue(self, job: WebhookJob) -> str:
        message = WebhookQueueMessage.from_job(job)
        await self._redis.lpush(self._queue_key, message.model_dump_json())
        return message.message_id

    async def reserve(self, timeout_sec: int = 5) -> tuple[WebhookQueueMessage, str] | None:
        timeout = int(timeout_sec) if timeout_sec is not None else 0

        raw = await self._redis.blmove(
            self._queue_key,
            self._processing_key,
            timeout=timeout,
            src="RIGHT",
            dest="LEFT",
        )

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