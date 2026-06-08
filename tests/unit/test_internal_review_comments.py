import importlib
import sys
import types


def _load_internal_module():
    fake_webhooks = types.ModuleType("apps.api.routes.webhooks")
    fake_webhooks.get_webhook_queue = lambda: None

    fake_artifacts = types.ModuleType("infra.storage.artifacts")
    fake_artifacts.load_run = lambda _run_id: None
    fake_artifacts.record_run_event = lambda *_args, **_kwargs: None

    fake_review_requests = types.ModuleType("infra.storage.review_requests")
    fake_review_requests.get_review_request = lambda _request_id: None
    fake_review_requests.mark_review_request_applied = lambda **_kwargs: None
    fake_review_requests.record_review_decision = lambda **_kwargs: {}

    sys.modules["apps.api.routes.webhooks"] = fake_webhooks
    sys.modules["infra.storage.artifacts"] = fake_artifacts
    sys.modules["infra.storage.review_requests"] = fake_review_requests
    sys.modules.pop("apps.api.routes.internal", None)
    return importlib.import_module("apps.api.routes.internal")


class _GitHubClient:
    comments: list[dict] = []

    def comment_on_pull_request(self, *, repo: str, pull_number: int, body: str) -> dict:
        self.comments.append({"repo": repo, "pull_number": pull_number, "body": body})
        return {"id": 1}

    def close(self) -> None:
        return None


def test_disapproved_review_comment_uses_blocking_findings(monkeypatch) -> None:
    internal = _load_internal_module()
    _GitHubClient.comments = []
    monkeypatch.setattr(internal, "GitHubClient", lambda: _GitHubClient())

    error = internal._comment_on_disapproved_review(
        {
            "repository": "acme/repo",
            "pull_request_number": 8,
            "blocking_findings": [
                {
                    "summary": "The change needs reviewer confirmation.",
                    "suggested_fix": "Confirm the expected behavior before merging.",
                }
            ],
        }
    )

    assert error == ""
    assert _GitHubClient.comments[0]["repo"] == "acme/repo"
    assert "Confirm the expected behavior" in _GitHubClient.comments[0]["body"]
