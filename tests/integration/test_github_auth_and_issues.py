import pytest

from core.contracts.enums import GitHubIssuePickStrategy
from infra.github import issues as github_issues
from infra.github.auth import resolve_github_token
from infra.github.auth import resolve_optional_github_token


def test_resolve_github_token_prefers_explicit_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setenv("GH_TOKEN", "fallback-token")
    assert resolve_github_token("explicit-token") == "explicit-token"
    assert resolve_github_token(None) == "env-token"
    assert resolve_optional_github_token(None) == "env-token"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert resolve_github_token(None) == "fallback-token"
    monkeypatch.delenv("GH_TOKEN")
    with pytest.raises(ValueError, match="Missing GitHub token"):
        resolve_github_token(None)
    assert resolve_optional_github_token(None) is None


def test_resolve_issue_reference_supports_url_and_number() -> None:
    repo, number = github_issues.resolve_issue_reference(12, repo="acme/repo")
    assert (repo, number) == ("acme/repo", 12)
    repo2, number2 = github_issues.resolve_issue_reference("#34", repo="acme/repo")
    assert (repo2, number2) == ("acme/repo", 34)
    repo3, number3 = github_issues.resolve_issue_reference(
        "https://github.com/org/project/issues/78"
    )
    assert (repo3, number3) == ("org/project", 78)
    with pytest.raises(ValueError, match="Unsupported GitHub issue URL format"):
        github_issues.resolve_issue_reference("https://github.com/org/project/pulls/78")
    with pytest.raises(ValueError, match="repo is required"):
        github_issues.resolve_issue_reference("45")


def test_pick_issue_strategies() -> None:
    issues = [
        {"id": 1, "created_at": "2024-01-01T00:00:00Z", "comments": 10},
        {"id": 2, "created_at": "2024-02-01T00:00:00Z", "comments": 1},
        {"id": 3, "created_at": "2024-03-01T00:00:00Z", "comments": 5},
    ]
    assert github_issues.pick_issue(issues, strategy=GitHubIssuePickStrategy.OLDEST_OPEN)["id"] == 1
    assert github_issues.pick_issue(issues, strategy=GitHubIssuePickStrategy.NEWEST_OPEN)["id"] == 3
    assert (
        github_issues.pick_issue(issues, strategy=GitHubIssuePickStrategy.LEAST_COMMENTS)["id"] == 2
    )
    assert (
        github_issues.pick_issue(issues, strategy=GitHubIssuePickStrategy.MOST_COMMENTS)["id"] == 1
    )
    with pytest.raises(ValueError, match="No issues available"):
        github_issues.pick_issue([])
    with pytest.raises(ValueError, match="Unknown issue pick strategy"):
        github_issues.pick_issue(issues, strategy="unknown")


class _FakeGitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.closed = False

    def list_issues(self, repo: str, **kwargs):
        return [{"number": 1, "repo": repo, "kwargs": kwargs}]

    def get_issue(self, repo: str, issue_number: int):
        return {"number": issue_number, "repo": repo, "title": "Bug", "body": "Body"}

    def list_issue_comments(self, repo: str, issue_number: int, **kwargs):
        return [
            {"id": 1, "body": "hello", "repo": repo, "issue_number": issue_number, "kwargs": kwargs}
        ]

    def close(self) -> None:
        self.closed = True


def test_get_issues_and_details_use_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_issues, "GitHubClient", _FakeGitHubClient)
    results = github_issues.get_issues("acme/repo", token="t")
    assert results[0]["repo"] == "acme/repo"
    issue = github_issues.get_issue_details(5, repo="acme/repo", include_comments=False)
    assert issue["number"] == 5
    with_comments = github_issues.get_issue_details(
        "https://github.com/acme/repo/issues/8",
        include_comments=True,
        comments_per_page=15,
        comments_page=2,
    )
    assert with_comments["number"] == 8
    assert with_comments["comments_items"][0]["kwargs"]["per_page"] == 15
    assert with_comments["comments_items"][0]["kwargs"]["page"] == 2


def test_get_and_pick_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_issues,
        "get_issues",
        lambda *args, **kwargs: [
            {"id": "a", "created_at": "2024-01-01T00:00:00Z", "comments": 2},
            {"id": "b", "created_at": "2024-02-01T00:00:00Z", "comments": 0},
        ],
    )
    chosen = github_issues.get_and_pick_issue(
        "acme/repo",
        strategy=GitHubIssuePickStrategy.LEAST_COMMENTS,
    )
    assert chosen["id"] == "b"
