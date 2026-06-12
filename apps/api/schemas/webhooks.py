from pydantic import BaseModel, Field


class GitHubWebhookResponse(BaseModel):
    status: str = "accepted"
    event_type: str
    delivery_id: str
    queued_runs: int = 0
    duplicate: bool = False
    ignored_reason: str = Field(default="")
