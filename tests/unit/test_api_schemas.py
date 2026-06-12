import pytest
from pydantic import ValidationError

from apps.api.schemas.webhooks import GitHubWebhookResponse


def test_webhook_response_defaults_and_fields() -> None:
    payload = GitHubWebhookResponse(
        event_type="issues",
        delivery_id="d-1",
    )
    assert payload.status == "accepted"
    assert payload.queued_runs == 0
    assert payload.duplicate is False
    assert payload.ignored_reason == ""
    assert payload.event_type == "issues"
    assert payload.delivery_id == "d-1"


def test_webhook_response_requires_event_type_and_delivery() -> None:
    with pytest.raises(ValidationError):
        GitHubWebhookResponse()
