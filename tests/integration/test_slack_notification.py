import uuid

import pytest

from core.orchestrator.models import RunModel
from core.orchestrator.models import StageResult
from core.orchestrator.models import StageStatus
from infra.slack.notification import build_review_action_token
from infra.slack.notification import decode_review_action_token
from infra.slack.notification import send_needs_review_notification
from infra.slack.notification import send_review_decision_notification


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _run() -> RunModel:
    return RunModel(
        run_id=uuid.UUID("4bf96c14-423f-431c-b172-b6e74585176a"),
        state="needs_review",
        repository="acme/repo",
        issue_number=14,
    )


def _stage_result() -> StageResult:
    return StageResult(
        stage="triage",
        status=StageStatus.NEEDS_REVIEW,
        outputs={"summary": "summary fallback"},
        notes={"reason": "policy blocked"},
    )


def test_review_action_token_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEW_ACTION_TOKEN_SECRET", "secret")
    monkeypatch.setenv("REVIEW_ACTION_TTL_SEC", "120")
    monkeypatch.setattr("infra.slack.notification.time.time", lambda: 1000)

    token = build_review_action_token("request-1", "approved")
    request_id, decision = decode_review_action_token(token)

    assert request_id == "request-1"
    assert decision == "approved"


def test_review_action_token_invalid_and_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEW_ACTION_TOKEN_SECRET", "secret")
    monkeypatch.setenv("REVIEW_ACTION_TTL_SEC", "1")
    monkeypatch.setattr("infra.slack.notification.time.time", lambda: 200)

    with pytest.raises(ValueError, match="Invalid decision"):
        build_review_action_token("request-1", "unknown")

    token = build_review_action_token("request-2", "disapproved")
    monkeypatch.setattr("infra.slack.notification.time.time", lambda: 500)
    with pytest.raises(ValueError, match="Token expired"):
        decode_review_action_token(token)


def test_send_needs_review_notification_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_NOTIFY_NEEDS_REVIEW", "false")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/webhook")

    result = send_needs_review_notification(_run(), _stage_result(), {"request_id": "rq1"})

    assert result == {"sent": False, "message_ref": "", "reason": "disabled"}


def test_send_needs_review_notification_missing_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_NOTIFY_NEEDS_REVIEW", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    missing_webhook = send_needs_review_notification(_run(), _stage_result(), {"request_id": "rq1"})
    assert missing_webhook == {"sent": False, "message_ref": "", "reason": "missing_webhook"}
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setenv("REVIEW_ACTION_TOKEN_SECRET", "secret")
    missing_request = send_needs_review_notification(_run(), _stage_result(), {"request_id": ""})
    assert missing_request == {"sent": False, "message_ref": "", "reason": "missing_request_id"}


def test_send_needs_review_notification_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse(200, "ok")

    monkeypatch.setenv("SLACK_NOTIFY_NEEDS_REVIEW", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setenv("SLACK_TIMEOUT_SEC", "9")
    monkeypatch.setenv("AUTOPR_PUBLIC_BASE_URL", "https://autopr.internal")
    monkeypatch.setenv("REVIEW_ACTION_TOKEN_SECRET", "secret")
    monkeypatch.setattr("infra.slack.notification.time.time", lambda: 1700)
    monkeypatch.setattr("infra.slack.notification.requests.post", _fake_post)

    result = send_needs_review_notification(_run(), _stage_result(), {"request_id": "rq1"})

    assert result == {
        "sent": True,
        "message_ref": "4bf96c14-423f-431c-b172-b6e74585176a:triage:1700",
        "reason": "ok",
    }
    assert calls[0]["url"] == "https://example.invalid/webhook"
    assert calls[0]["timeout"] == 9
    payload = calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["text"] == "AutoPR needs review for acme/repo [triage]"
    blocks = payload["blocks"]
    assert blocks[2]["text"]["text"] == "*Reason*\npolicy blocked"
    urls = [item["url"] for item in blocks[3]["elements"]]
    assert "https://autopr.internal/runs/4bf96c14-423f-431c-b172-b6e74585176a" in urls


def test_send_needs_review_notification_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        del url
        del json
        del timeout
        return _FakeResponse(502, "x" * 900)

    monkeypatch.setenv("SLACK_NOTIFY_NEEDS_REVIEW", "1")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setenv("REVIEW_ACTION_TOKEN_SECRET", "secret")
    monkeypatch.setattr("infra.slack.notification.requests.post", _fake_post)

    result = send_needs_review_notification(_run(), _stage_result(), {"request_id": "rq1"})

    assert result["sent"] is False
    assert result["message_ref"] == ""
    assert result["reason"] == "slack_http_502"
    assert len(result["response_text"]) == 500


def test_send_review_decision_notification_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Invalid token format"):
        decode_review_action_token("bad-token")

    invalid = send_review_decision_notification(request_id="r1", decision="bad")
    assert invalid == {"sent": False, "reason": "invalid_decision"}

    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    missing = send_review_decision_notification(request_id="r1", decision="approved")
    assert missing == {"sent": False, "reason": "missing_webhook"}


def test_send_review_decision_notification_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def _ok_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse(200, "ok")

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/webhook")
    monkeypatch.setenv("SLACK_TIMEOUT_SEC", "6")
    monkeypatch.setattr("infra.slack.notification.requests.post", _ok_post)

    success = send_review_decision_notification(
        request_id="r-ok",
        decision="approved",
        reviewer="nidhi",
        reason="looks good",
    )

    assert success == {"sent": True, "reason": "ok"}
    assert calls[0]["timeout"] == 6
    payload = calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["text"] == "AutoPR decision: Approved (r-ok)"
    assert payload["blocks"][2]["text"]["text"] == "*Reviewer*\nnidhi"
    assert payload["blocks"][3]["text"]["text"] == "*Reason*\nlooks good"

    def _err_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        del url
        del json
        del timeout
        return _FakeResponse(429, "limited")

    monkeypatch.setattr("infra.slack.notification.requests.post", _err_post)
    failed = send_review_decision_notification(request_id="r-err", decision="disapproved")
    assert failed["sent"] is False
    assert failed["reason"] == "slack_http_429"
    assert failed["response_text"] == "limited"
