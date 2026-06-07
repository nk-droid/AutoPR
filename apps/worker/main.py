import asyncio
import logging

from opentelemetry.trace import SpanKind

from infra.ray.runtime import ensure_ray_initialized
from infra.github.webhook_handler import dispatch_resume_job, dispatch_webhook_job
from infra.redis.webhook_queue import RedisWebhookQueue

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
                await queue.fail(message, raw, str(exc))
                logger.exception("failed message_id=%s", message.message_id)
                QUEUE_MESSAGES_TOTAL.labels(
                    action="failed",
                    run_type=message.run_type.value,
                    result="error",
                ).inc()
            finally:
                detach_trace_context(token)
    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(run())
