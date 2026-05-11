from typing import Any
from urllib.parse import urlparse
from infra.github.client import GitHubClient

def get_issues(
    repo: str,
    *,
    state: str = "open",
    labels: str | None = None,
    per_page: int = 30,
    page: int = 1,
    sort: str = "created",
    direction: str = "asc",
    token: str | None = None,
) -> list[dict]:
    client = GitHubClient(token=token)
    try:
        return client.list_issues(
            repo,
            state=state,
            labels=labels,
            per_page=per_page,
            page=page,
            sort=sort,
            direction=direction,
        )
    finally:
        client.close()

def resolve_issue_reference(issue_reference: str | int, repo: str | None = None) -> tuple[str, int]:
    if isinstance(issue_reference, int):
        if not repo:
            raise ValueError("repo is required when issue reference is numeric")
        return repo, issue_reference
    reference = issue_reference.strip()
    if reference.startswith("http://") or reference.startswith("https://"):
        parsed = urlparse(reference)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 4 or path_parts[2] != "issues":
            raise ValueError(f"Unsupported GitHub issue URL format: {issue_reference}")
        owner = path_parts[0]
        repo_name = path_parts[1]
        try:
            issue_number = int(path_parts[3])
        except ValueError as exc:
            raise ValueError(f"Issue number must be numeric in URL: {issue_reference}") from exc
        return f"{owner}/{repo_name}", issue_number
    if reference.startswith("#"):
        reference = reference[1:]
    if reference.isdigit():
        if not repo:
            raise ValueError("repo is required when issue reference is numeric")
        return repo, int(reference)
    raise ValueError(
        "Issue reference must be an issue number (e.g. 123 or #123) "
        "or a full GitHub issue URL."
    )

def get_issue_details(
    issue_reference: str | int,
    *,
    repo: str | None = None,
    token: str | None = None,
    include_comments: bool = False,
    comments_per_page: int = 20,
    comments_page: int = 1,
) -> dict:
    resolved_repo, issue_number = resolve_issue_reference(issue_reference, repo)
    client = GitHubClient(token=token)
    try:
        issue = client.get_issue(resolved_repo, issue_number)
        if include_comments:
            issue_with_comments = dict(issue)
            issue_with_comments["comments_items"] = client.list_issue_comments(
                resolved_repo,
                issue_number,
                per_page=comments_per_page,
                page=comments_page,
            )
            return issue_with_comments
        return issue
    finally:
        client.close()

def _issue_sort_key(issue: dict[str, Any], field: str) -> str:
    value = issue.get(field)
    if isinstance(value, str):
        return value
    return ""

def pick_issue(issues: list[dict], *, strategy: str = "oldest_open") -> dict:
    if not issues:
        raise ValueError("No issues available to pick from")
    if strategy == "oldest_open":
        return sorted(issues, key=lambda issue: _issue_sort_key(issue, "created_at"))[0]
    if strategy == "newest_open":
        return sorted(issues, key=lambda issue: _issue_sort_key(issue, "created_at"), reverse=True)[0]
    if strategy == "least_comments":
        return sorted(
            issues,
            key=lambda issue: (
                int(issue.get("comments", 0)),
                _issue_sort_key(issue, "created_at"),
            ),
        )[0]
    if strategy == "most_comments":
        return sorted(
            issues,
            key=lambda issue: (
                int(issue.get("comments", 0)),
                _issue_sort_key(issue, "created_at"),
            ),
            reverse=True,
        )[0]
    raise ValueError(f"Unknown issue pick strategy: {strategy}")

def get_and_pick_issue(
    repo: str,
    *,
    strategy: str = "oldest_open",
    state: str = "open",
    labels: str | None = None,
    per_page: int = 30,
    page: int = 1,
    token: str | None = None,
) -> dict:
    issues = get_issues(
        repo,
        state=state,
        labels=labels,
        per_page=per_page,
        page=page,
        token=token,
    )
    return pick_issue(issues, strategy=strategy)
