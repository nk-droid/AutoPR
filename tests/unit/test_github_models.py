from datetime import datetime
from datetime import timezone

import pytest

from infra.github import models as github_models


def test_issue_payload_populates_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc).isoformat()

    class _FakeClient:
        def list_issue_comments(self, repo: str, issue_number: int):
            return [
                {
                    "url": f"https://github.com/{repo}/issues/{issue_number}#issuecomment-1",
                    "body": "hello",
                    "created_at": now,
                    "updated_at": now,
                }
            ]

    monkeypatch.setattr(github_models, "GitHubClient", lambda: _FakeClient())
    payload = github_models.IssuePayload.model_validate(
        {
            "action": "opened",
            "issue": {
                "number": 1,
                "url": "https://github.com/acme/repo/issues/1",
                "title": "Bug",
                "body": "Details",
                "created_at": now,
                "updated_at": now,
            },
            "repository": {
                "full_name": "acme/repo",
                "url": "https://github.com/acme/repo",
                "default_branch": "main",
            },
        }
    )
    assert len(payload.issue.comment_list) == 1
    assert payload.issue.comment_list[0].body == "hello"
