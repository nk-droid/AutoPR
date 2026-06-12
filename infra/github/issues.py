import logging
from typing import Any
from urllib.parse import urlparse
from core.contracts.enums import (
    GitHubIssuePickStrategy,
    GitHubIssueSort,
    GitHubIssueState,
    GitHubPathSegment,
    GitHubSortDirection,
)
from infra.github.client import GitHubClient

logger = logging.getLogger(__name__)


def get_issues(
    repo: str,
    *,
    state: GitHubIssueState | str = GitHubIssueState.OPEN,
    labels: str | None = None,
    per_page: int = 30,
    page: int = 1,
    sort: GitHubIssueSort | str = GitHubIssueSort.CREATED,
    direction: GitHubSortDirection | str = GitHubSortDirection.ASC,
    token: str | None = None,
) -> list[dict]:
    """
    Fetch repository issues using the configured GitHub issue filters.

    Args:
        repo: Repository full name in owner/name form.
        state: GitHub issue state to request.
        labels: Optional comma-separated label filter.
        per_page: Number of issues requested per page.
        page: Page number requested from GitHub.
        sort: GitHub issue sort field.
        direction: Sort direction for GitHub results.
        token: Optional GitHub token override.

    Returns:
        Issue dictionaries returned by GitHub after client filtering.
    """

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
    """
    Resolve an issue number, shorthand, or GitHub URL into repository details.

    Args:
        issue_reference: Issue number, #number shorthand, or full issue URL.
        repo: Repository full name required for numeric references.

    Returns:
        Repository full name and numeric issue number.
    """

    if isinstance(issue_reference, int):
        if not repo:
            raise ValueError("repo is required when issue reference is numeric")
        return repo, issue_reference
    reference = issue_reference.strip()
    if reference.startswith("http://") or reference.startswith("https://"):
        parsed = urlparse(reference)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 4 or path_parts[2] != GitHubPathSegment.ISSUES.value:
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
        "Issue reference must be an issue number (e.g. 123 or #123) or a full GitHub issue URL."
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
    """
    Fetch a resolved GitHub issue and optionally include comment context.

    Args:
        issue_reference: Issue number, #number shorthand, or full issue URL.
        repo: Repository full name required for numeric references.
        token: Optional GitHub token override.
        include_comments: Whether to attach issue comments to the payload.
        comments_per_page: Number of comments requested per page.
        comments_page: Page number requested for comments.

    Returns:
        Issue payload, optionally including comments_items.
    """

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


def _comment_count(issue: dict[str, Any]) -> int:
    value = issue.get("comments")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def pick_issue(
    issues: list[dict],
    *,
    strategy: GitHubIssuePickStrategy | str = GitHubIssuePickStrategy.OLDEST_OPEN,
) -> dict:
    """
    Select one issue from candidates using the configured queueing strategy.

    Args:
        issues: Candidate issue payloads fetched from GitHub.
        strategy: Selection strategy for prioritizing the next issue.

    Returns:
        Chosen issue payload.
    """

    if not issues:
        raise ValueError("No issues available to pick from")
    try:
        strategy_enum = GitHubIssuePickStrategy(strategy)
    except ValueError as exc:
        raise ValueError(f"Unknown issue pick strategy: {strategy}") from exc
    if strategy_enum == GitHubIssuePickStrategy.OLDEST_OPEN:
        return sorted(issues, key=lambda issue: _issue_sort_key(issue, "created_at"))[0]
    if strategy_enum == GitHubIssuePickStrategy.NEWEST_OPEN:
        return sorted(issues, key=lambda issue: _issue_sort_key(issue, "created_at"), reverse=True)[
            0
        ]
    if strategy_enum == GitHubIssuePickStrategy.LEAST_COMMENTS:
        return sorted(
            issues,
            key=lambda issue: (
                _comment_count(issue),
                _issue_sort_key(issue, "created_at"),
            ),
        )[0]
    if strategy_enum == GitHubIssuePickStrategy.MOST_COMMENTS:
        return sorted(
            issues,
            key=lambda issue: (
                _comment_count(issue),
                _issue_sort_key(issue, "created_at"),
            ),
            reverse=True,
        )[0]
    raise ValueError(f"Unknown issue pick strategy: {strategy}")


def get_and_pick_issue(
    repo: str,
    *,
    strategy: GitHubIssuePickStrategy | str = GitHubIssuePickStrategy.OLDEST_OPEN,
    state: GitHubIssueState | str = GitHubIssueState.OPEN,
    labels: str | None = None,
    per_page: int = 30,
    page: int = 1,
    token: str | None = None,
) -> dict:
    """
    Fetch available issues and select the next one for AutoPR processing.

    Args:
        repo: Repository full name in owner/name form.
        strategy: Selection strategy for prioritizing the next issue.
        state: GitHub issue state to request.
        labels: Optional comma-separated label filter.
        per_page: Number of issues requested per page.
        page: Page number requested from GitHub.
        token: Optional GitHub token override.

    Returns:
        Chosen issue payload.
    """

    issues = get_issues(
        repo,
        state=state,
        labels=labels,
        per_page=per_page,
        page=page,
        token=token,
    )
    chosen = pick_issue(issues, strategy=strategy)
    logger.debug(
        "issue selected",
        extra={
            "event": "issue_selected",
            "repo": repo,
            "issue_number": chosen.get("number"),
            "strategy": GitHubIssuePickStrategy(strategy).value,
            "candidate_count": len(issues),
        },
    )
    return chosen
