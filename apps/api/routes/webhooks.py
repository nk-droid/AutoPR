import logging
from functools import lru_cache

from opentelemetry import trace
from fastapi import APIRouter, Header, Request, HTTPException

from apps.api.schemas.webhooks import GitHubWebhookResponse
from infra.github.webhook_handler import handle_github_webhook
from infra.redis.webhook_queue import RedisWebhookQueue

from core.contracts.enums import WebhookResultType

from observability.tracing import traced
from observability.metrics import WEBHOOKS_TOTAL, WEBHOOK_JOBS_ENQUEUED_TOTAL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@lru_cache
def get_webhook_queue() -> RedisWebhookQueue:
    """
    Return the process-local Redis webhook queue singleton.

    Returns:
        Redis webhook queue configured from environment variables.
    """

    return RedisWebhookQueue.from_env()

def _github_webhook_attributes(
    request: Request,
    x_github_event: str,
    x_github_delivery: str,
    x_hub_signature_256: str | None = None,
) -> dict:
    """
    Build trace attributes for the GitHub webhook API route.

    Args:
        request: FastAPI request being handled.
        x_github_event: GitHub event header value.
        x_github_delivery: GitHub delivery identifier.
        x_hub_signature_256: Optional GitHub signature header.

    Returns:
        Span attributes describing the inbound webhook request.
    """

    return {
        "http.request.method": request.method,
        "http.route": "/webhooks/github",
        "github.webhook.event": x_github_event,
        "github.webhook.delivery_id": x_github_delivery,
        "github.webhook.signature_present": x_hub_signature_256 is not None,
    }

@router.post("/github", response_model=GitHubWebhookResponse, status_code=202)
@traced("api.webhooks.github", attributes=_github_webhook_attributes)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_github_delivery: str = Header(..., alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> GitHubWebhookResponse:
    """
    Accept a GitHub webhook, filter it, and enqueue resulting jobs.

    Args:
        request: FastAPI request containing the raw webhook body.
        x_github_event: GitHub event header value.
        x_github_delivery: GitHub delivery identifier.
        x_hub_signature_256: Optional GitHub signature header.

    Returns:
        API response describing accepted, ignored, and queued work.
    """

    body = await request.body()

    logger.info(
        "webhook received",
        extra={
            "event": "webhook_received",
            "event_type": x_github_event,
            "delivery_id": x_github_delivery,
            "signature_present": x_hub_signature_256 is not None,
        },
    )

    # Parse and filter webhook payload synchronously before queuing background work.
    result = handle_github_webhook(
        event_type=x_github_event,
        delivery_id=x_github_delivery,
        body=body,
        signature_256=x_hub_signature_256,
    )

    result_label = WebhookResultType.ACCEPTED if result.jobs else WebhookResultType.IGNORED
    WEBHOOKS_TOTAL.labels(event_type=x_github_event, result=result_label).inc()

    if not result.jobs:
        logger.info(
            "webhook ignored",
            extra={
                "event": "webhook_ignored",
                "event_type": x_github_event,
                "delivery_id": x_github_delivery,
                "ignored_reason": result.ignored_reason,
                "duplicate": result.duplicate,
            },
        )

    span = trace.get_current_span()
    span.set_attribute("autopr.webhook.jobs", len(result.jobs))
    span.set_attribute("autopr.webhook.duplicate", result.duplicate)
    if result.ignored_reason:
        span.set_attribute("autopr.webhook.ignored_reason", result.ignored_reason)

    # Enqueue background work for accepted webhooks; failures here indicate transient issues with
    # the queue and should be retried by GitHub.
    queue = get_webhook_queue()
    try:
        for job in result.jobs:
            await queue.enqueue(job)
            WEBHOOK_JOBS_ENQUEUED_TOTAL.labels(run_type=job.run_type.value).inc()
    except Exception as exc:
        WEBHOOKS_TOTAL.labels(event_type=x_github_event, result="queue_error").inc()
        span.record_exception(exc)
        span.set_attribute("autopr.webhook.queue_error", exc.__class__.__name__)
        logger.error(
            "webhook enqueue failed",
            extra={
                "event": "webhook_enqueue_failed",
                "event_type": x_github_event,
                "delivery_id": x_github_delivery,
                "error": exc.__class__.__name__,
            },
        )
        raise HTTPException(status_code=503, detail="Webhook queue unavailable") from exc

    if result.jobs:
        logger.info(
            "webhook jobs enqueued",
            extra={
                "event": "webhook_jobs_enqueued",
                "event_type": x_github_event,
                "delivery_id": x_github_delivery,
                "job_count": len(result.jobs),
                "run_type": result.jobs[0].run_type.value,
            },
        )

    return GitHubWebhookResponse(
        status=WebhookResultType.ACCEPTED,
        event_type=x_github_event,
        delivery_id=x_github_delivery,
        queued_runs=len(result.jobs),
        duplicate=result.duplicate,
        ignored_reason=result.ignored_reason,
    )
