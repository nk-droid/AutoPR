import asyncio
import logging

from infra.ray.runtime import ensure_ray_initialized
from infra.github.webhook_handler import dispatch_webhook_job
from infra.redis.webhook_queue import RedisWebhookQueue

from observability.tracing import configure_tracing
from observability.metrics import QUEUE_MESSAGES_TOTAL, start_worker_metrics_server

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
            try:
                job = message.to_job()
                result = dispatch_webhook_job(job)
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
        await queue.close()


if __name__ == "__main__":
    asyncio.run(run())
