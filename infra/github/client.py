from typing import Any
import httpx
from core.contracts.enums import GitHubIssueSort, GitHubIssueState, GitHubSortDirection
from infra.github.auth import resolve_github_token, resolve_optional_github_token

class GitHubAPIError(RuntimeError):
    def __init__(
        self,
        *,
        method: str,
        url: str,
        status_code: int,
        message: str,
        response_payload: Any | None = None,
    ) -> None:
        details = f"GitHub API error {status_code} for {method.upper()} {url}: {message}"
        super().__init__(details)
        self.method = method
        self.url = url
        self.status_code = status_code
        self.message = message
        self.response_payload = response_payload

def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""

def _stringify_error_item(item: Any) -> str:
    if isinstance(item, dict):
        message = _as_text(item.get("message"))
        resource = _as_text(item.get("resource"))
        field = _as_text(item.get("field"))
        code = _as_text(item.get("code"))
        parts = [part for part in [resource, field, code] if part]
        prefix = "/".join(parts)
        if prefix and message:
            return f"{prefix}: {message}"
        if prefix:
            return prefix
        if message:
            return message
    item_text = str(item).strip()
    return item_text

def _extract_error_details(payload: Any) -> str:
    if isinstance(payload, dict):
        message = _as_text(payload.get("message"))
        errors = payload.get("errors")
        errors_text: list[str] = []
        if isinstance(errors, list):
            for error_item in errors:
                item_text = _stringify_error_item(error_item)
                if item_text:
                    errors_text.append(item_text)
        details: list[str] = []
        if message:
            details.append(message)
        if errors_text:
            details.append("; ".join(errors_text))
        documentation_url = _as_text(payload.get("documentation_url"))
        if documentation_url:
            details.append(f"docs: {documentation_url}")
        if details:
            return " | ".join(details)
    payload_text = str(payload).strip()
    return payload_text

class GitHubClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = resolve_optional_github_token(token)
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _headers(self, *, require_auth: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif require_auth:
            resolve_github_token(None)
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        require_auth: bool = False,
    ) -> Any:
        url = f"{self.base_url}{endpoint}"
        response = self.client.request(
            method=method,
            url=url,
            headers=self._headers(require_auth=require_auth),
            json=json,
            params=params,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            payload: Any
            try:
                payload = response.json()
            except Exception:
                payload = response.text
            details = _extract_error_details(payload) or str(exc)
            raise GitHubAPIError(
                method=method,
                url=url,
                status_code=response.status_code,
                message=details,
                response_payload=payload,
            ) from exc
        return response.json()

    def list_issues(
        self,
        repo: str,
        *,
        state: GitHubIssueState | str = GitHubIssueState.OPEN,
        labels: str | None = None,
        per_page: int = 30,
        page: int = 1,
        sort: GitHubIssueSort | str = GitHubIssueSort.CREATED,
        direction: GitHubSortDirection | str = GitHubSortDirection.ASC,
    ) -> list[dict]:
        query: dict[str, Any] = {
            "state": GitHubIssueState(state).value,
            "per_page": per_page,
            "page": page,
            "sort": GitHubIssueSort(sort).value,
            "direction": GitHubSortDirection(direction).value,
        }
        if labels:
            query["labels"] = labels
        payload = self._request("GET", f"/repos/{repo}/issues", params=query)
        if not isinstance(payload, list):
            raise ValueError("Unexpected GitHub response while listing issues")
        return [item for item in payload if isinstance(item, dict) and "pull_request" not in item]

    def get_issue(self, repo: str, issue_number: int) -> dict:
        payload = self._request("GET", f"/repos/{repo}/issues/{issue_number}")
        if not isinstance(payload, dict):
            raise ValueError("Unexpected GitHub response while getting issue")
        return payload

    def list_issue_comments(
        self,
        repo: str,
        issue_number: int,
        *,
        per_page: int = 20,
        page: int = 1,
    ) -> list[dict]:
        payload = self._request(
            "GET",
            f"/repos/{repo}/issues/{issue_number}/comments",
            params={
                "per_page": per_page,
                "page": page,
            },
        )
        if not isinstance(payload, list):
            raise ValueError("Unexpected GitHub response while listing issue comments")
        return [item for item in payload if isinstance(item, dict)]

    def create_pull_request(
        self,
        *,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
        head_repo: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        if head_repo:
            payload["head_repo"] = head_repo
        payload = self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json=payload,
            require_auth=True,
        )
        if not isinstance(payload, dict):
            raise ValueError("Unexpected GitHub response while creating pull request")
        return payload

    def get_pull_request(self, repo: str, pull_number: int) -> dict:
        payload = self._request("GET", f"/repos/{repo}/pulls/{pull_number}")
        if not isinstance(payload, dict):
            raise ValueError("Unexpected GitHub response while getting pull request")
        return payload

    def comment_on_pull_request(self, *, repo: str, pull_number: int, body: str) -> dict:
        payload = self._request(
            "POST",
            f"/repos/{repo}/issues/{pull_number}/comments",
            json={"body": body},
            require_auth=True,
        )
        if not isinstance(payload, dict):
            raise ValueError("Unexpected GitHub response while commenting on pull request")
        return payload

    def merge_pull_request(
        self,
        *,
        repo: str,
        pull_number: int,
        merge_method: str = "squash",
        commit_title: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        response_payload = self._request(
            "PUT",
            f"/repos/{repo}/pulls/{pull_number}/merge",
            json=payload,
            require_auth=True,
        )
        if not isinstance(response_payload, dict):
            raise ValueError("Unexpected GitHub response while merging pull request")
        return response_payload

def get_issue(repo: str, issue_number: int) -> dict:
    client = GitHubClient()
    try:
        return client.get_issue(repo, issue_number)
    finally:
        client.close()
