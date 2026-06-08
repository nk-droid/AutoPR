import uuid

from core.orchestrator.models import RunModel, StageResult, StageStatus
from infra.slack.notification import send_needs_review_notification


class _Response:
    status_code = 200
    text = "ok"


def test_slack_notification_includes_llm_review_fields(monkeypatch) -> None:
    calls: list[dict] = []

    def _post(url: str, json: dict, timeout: int) -> _Response:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setenv("SLACK_NOTIFY_NEEDS_REVIEW", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setenv("REVIEW_ACTION_TOKEN_SECRET", "secret")
    monkeypatch.setattr("infra.slack.notification.requests.post", _post)
    monkeypatch.setattr("infra.slack.notification.time.time", lambda: 1000)

    run = RunModel(
        run_id=uuid.UUID("4bf96c14-423f-431c-b172-b6e74585176a"),
        state="REVIEW_PENDING",
        repository="acme/repo",
    )
    result = StageResult(
        stage="review",
        status=StageStatus.NEEDS_REVIEW,
        notes={
            "reason": "LLM merge-risk review requires human approval before merge.",
            "merge_risk": "high",
            "confidence": "medium",
            "blocking_findings": [
                {"severity": "high", "summary": "A behavior change needs explicit review."}
            ],
        },
    )

    sent = send_needs_review_notification(run, result, {"request_id": "rq-1"})

    assert sent["sent"] is True
    blocks_text = "\n".join(str(block) for block in calls[0]["json"]["blocks"])
    assert "Merge risk" in blocks_text
    assert "Confidence" in blocks_text
    assert "A behavior change needs explicit review." in blocks_text
