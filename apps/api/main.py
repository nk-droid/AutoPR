import logging
import os
from fastapi import FastAPI
from contextlib import asynccontextmanager

from prometheus_client import make_asgi_app
from observability.logging import setup_logging
from observability.tracing import configure_tracing

from infra.redis.webhook_queue import start_queue_depth_sampler
from apps.api.routes.internal import router as internal_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.webhooks import get_webhook_queue, router as webhooks_router

setup_logging(service_name="autopr-api")
configure_tracing(service_name="autopr-api")

logger = logging.getLogger(__name__)
logger.info(
    "service starting",
    extra={
        "event": "service_starting",
        "service": "autopr-api",
        "env": os.getenv("AUTOPR_ENV", "local"),
        "log_exporter": os.getenv("AUTOPR_LOG_EXPORTER", "otlp"),
    },
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_queue_depth_sampler()
    logger.info("api ready", extra={"event": "service_ready", "service": "autopr-api"})
    try:
        yield
    finally:
        logger.info("api shutting down", extra={"event": "service_shutdown", "service": "autopr-api"})
        if get_webhook_queue.cache_info().currsize > 0:
            await get_webhook_queue().close()

app = FastAPI(title="AutoPR API", lifespan=lifespan)

app.include_router(webhooks_router)
app.include_router(runs_router)
app.include_router(internal_router)
app.mount("/metrics", make_asgi_app())