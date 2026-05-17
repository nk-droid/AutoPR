from fastapi import APIRouter, BackgroundTasks, Header, Request

from apps.api.schemas.webhooks import GitHubWebhookResponse
from infra.github.webhook_handler import dispatch_webhook_job, handle_github_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@router.post("/github", response_model=GitHubWebhookResponse, status_code=202)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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
    for job in result.jobs:
        background_tasks.add_task(dispatch_webhook_job, job)

    return GitHubWebhookResponse(
        status="accepted",
        event_type=x_github_event,
        delivery_id=x_github_delivery,
        queued_runs=len(result.jobs),
        duplicate=result.duplicate,
        ignored_reason=result.ignored_reason,
    )
