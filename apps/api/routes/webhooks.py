from functools import lru_cache

from fastapi import APIRouter, Header, Request, HTTPException

from apps.api.schemas.webhooks import GitHubWebhookResponse
from infra.github.webhook_handler import handle_github_webhook
from infra.redis.webhook_queue import RedisWebhookQueue

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@lru_cache
def get_webhook_queue() -> RedisWebhookQueue:
    return RedisWebhookQueue.from_env()

@router.post("/github", response_model=GitHubWebhookResponse, status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_github_delivery: str = Header(..., alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> GitHubWebhookResponse:
    body = await request.body()
    
    # Parse and filter webhook payload synchronously before queuing background work.
    result = handle_github_webhook(
        event_type=x_github_event,
        delivery_id=x_github_delivery,
        body=body,
        signature_256=x_hub_signature_256,
    )

    queue = get_webhook_queue()
    try:
        for job in result.jobs:
            await queue.enqueue(job)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Webhook queue unavailable") from exc

    return GitHubWebhookResponse(
        status="accepted",
        event_type=x_github_event,
        delivery_id=x_github_delivery,
        queued_runs=len(result.jobs),
        duplicate=result.duplicate,
        ignored_reason=result.ignored_reason,
    )
