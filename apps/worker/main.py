import asyncio

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

import logging
logger = logging.getLogger("autopr.worker")

configure_tracing(service_name="autopr-worker")

def _extract_repository(message: WebhookQueueMessage) -> str:
    """
    Extract repository identity from webhook or resume queue payloads.

    Args:
        message: Queue message being processed or dead-lettered.

    Returns:
        Repository full name when present, otherwise an empty string.
    """

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
    """
    Handle a message that has been moved to the dead-letter queue by recording it in persistent storage
    and sending a notification.

    Args:
        message: The message that was dead-lettered.
    """

    # Record the dead-lettered message
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

    # Send a notification about the dead-lettered message
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
    """Main worker loop that continuously reserves messages from the webhook queue and processes them."""

    # Start the metrics server to expose worker metrics.
    start_worker_metrics_server()

    # Initialize Ray runtime for distributed processing.
    ensure_ray_initialized()

    # Create a connection to the webhook queue and continuously process messages until shutdown.
    queue = RedisWebhookQueue.from_env()
    try:
        while True:
            reserved = await queue.reserve(timeout_sec=5)
            if reserved is None:
                continue

            # Each reserved message is processed within its own trace context.
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

                # If processing succeeds, acknowledge the message to remove it from the queue.
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
                # If processing fails, move the message to the dead-letter queue and record the failure.
                dead_lettered, failed_message = await queue.fail(message, raw, str(exc))

                logger.exception("failed message_id=%s", message.message_id)
                QUEUE_MESSAGES_TOTAL.labels(
                    action="failed",
                    run_type=message.run_type.value,
                    result="error",
                ).inc()

                # If the message was dead-lettered, send a notification and record it.
                if dead_lettered:
                    _handle_dead_letter(failed_message)
            finally:
                detach_trace_context(token)
    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(run())
