import asyncio
import logging

from opentelemetry.trace import SpanKind

from infra.ray.runtime import ensure_ray_initialized
from infra.github.webhook_handler import dispatch_resume_job, dispatch_webhook_job
from infra.redis.webhook_queue import RedisWebhookQueue, WebhookQueueMessage
from infra.slack.notification import send_dead_letter_notification
from infra.storage.dead_letter import record_dead_letter_job

from observability.tracing import (
    attach_trace_context,
    configure_tracing,
    detach_trace_context,
    get_tracer,
)
from observability.metrics import QUEUE_MESSAGES_TOTAL, start_worker_metrics_server

import dotenv
dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("autopr.worker")

configure_tracing(service_name="autopr-worker")

def _extract_repository(message: WebhookQueueMessage) -> str:
    payload = message.resume_payload if message.kind == "resume" else message.job_payload
    if isinstance(payload, dict):
        repo = payload.get("repository")
        if isinstance(repo, str) and repo:
            return repo
        context = payload.get("context")
        if isinstance(context, dict) and isinstance(context.get("repository"), str):
            return context["repository"]
    return ""

def _handle_dead_letter(message: WebhookQueueMessage) -> None:
    # A job that exhausted its retries: persist it and alert, best-effort so the
    # worker loop keeps draining even if Postgres or Slack is unavailable.
    repository = _extract_repository(message)
    payload = message.resume_payload if message.kind == "resume" else message.job_payload
    try:
        record_dead_letter_job(
            message_id=message.message_id,
            kind=message.kind,
            run_type=message.run_type.value,
            repository=repository,
            attempts=message.attempts,
            last_error=message.last_error,
            payload=payload if isinstance(payload, dict) else {},
        )
    except Exception:
        logger.exception("failed to persist dead-letter message_id=%s", message.message_id)
    try:
        send_dead_letter_notification(
            message_id=message.message_id,
            kind=message.kind,
            run_type=message.run_type.value,
            repository=repository,
            attempts=message.attempts,
            last_error=message.last_error,
        )
    except Exception:
        logger.exception("failed to notify dead-letter message_id=%s", message.message_id)
    QUEUE_MESSAGES_TOTAL.labels(
        action="dead_letter",
        run_type=message.run_type.value,
        result="error",
    ).inc()

async def run() -> None:
    start_worker_metrics_server()
    ensure_ray_initialized()
    queue = RedisWebhookQueue.from_env()
    try:
        while True:
            reserved = await queue.reserve(timeout_sec=5)
            if reserved is None:
                continue
            message, raw = reserved
            token = attach_trace_context(message.trace_context)
            try:
                with get_tracer().start_as_current_span(
                    "worker.process_job", kind=SpanKind.CONSUMER
                ) as span:
                    span.set_attribute("autopr.run_type", message.run_type.value)
                    span.set_attribute("autopr.message_id", message.message_id)
                    span.set_attribute("autopr.message_kind", message.kind)
                    if message.kind == "resume":
                        result = dispatch_resume_job(message.resume_payload)
                    else:
                        result = dispatch_webhook_job(message.to_job())
                await queue.ack(raw)
                logger.info(
                    "processed message_id=%s run_id=%s run_type=%s state=%s",
                    message.message_id,
                    result.run_id,
                    result.run_type,
                    result.state,
                )
                QUEUE_MESSAGES_TOTAL.labels(
                    action="processed",
                    run_type=message.run_type.value,
                    result="ok",
                ).inc()
            except Exception as exc:
                dead_lettered, failed_message = await queue.fail(message, raw, str(exc))
                logger.exception("failed message_id=%s", message.message_id)
                QUEUE_MESSAGES_TOTAL.labels(
                    action="failed",
                    run_type=message.run_type.value,
                    result="error",
                ).inc()
                if dead_lettered:
                    _handle_dead_letter(failed_message)
            finally:
                detach_trace_context(token)
    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(run())
