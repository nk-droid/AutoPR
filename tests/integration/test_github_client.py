import httpx
import pytest

from infra.github.client import GitHubAPIError
from infra.github.client import GitHubClient
from infra.github.client import _extract_error_details
from infra.github.client import _stringify_error_item


class _FakeResponse:
    def __init__(
        self, status_code: int, payload, text: str = "", headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)
        self.headers = headers or {"X-RateLimit-Remaining": "100"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.github.com/fake")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []
        self.closed = False

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def close(self) -> None:
        self.closed = True


def test_error_detail_helpers() -> None:
    item_text = _stringify_error_item(
        {"resource": "PullRequest", "field": "head", "code": "invalid", "message": "bad head"}
    )
    assert "PullRequest/head/invalid: bad head" == item_text
    detail = _extract_error_details(
        {
            "message": "Validation Failed",
            "errors": [{"resource": "PullRequest", "field": "base", "code": "invalid"}],
            "documentation_url": "https://docs.github.com",
        }
    )
    assert "Validation Failed" in detail
    assert "PullRequest/base/invalid" in detail
    assert "docs: https://docs.github.com" in detail


def test_request_success_and_auth_headers() -> None:
    response = _FakeResponse(200, [{"id": 1}, {"id": 2}])
    fake_client = _FakeHttpClient(response)
    client = GitHubClient(token="abc", client=fake_client)
    result = client.list_issues("acme/repo")
    assert len(result) == 2
    headers = fake_client.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer abc"
    assert headers["Accept"] == "application/vnd.github+json"
    client.close()
    assert fake_client.closed is False


def test_request_raises_github_api_error_with_payload() -> None:
    payload = {"message": "Validation Failed", "errors": [{"field": "head", "code": "invalid"}]}
    response = _FakeResponse(422, payload)
    fake_client = _FakeHttpClient(response)
    client = GitHubClient(token="abc", client=fake_client)
    with pytest.raises(GitHubAPIError) as exc_info:
        client.create_pull_request(repo="acme/repo", title="t", head="h", base="main")
    exc = exc_info.value
    assert exc.status_code == 422
    assert exc.response_payload == payload
    assert "Validation Failed" in str(exc)
