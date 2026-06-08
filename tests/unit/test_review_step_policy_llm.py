from typing import Any

import core.orchestrator.steps.review as review_steps
from core.orchestrator.models import MergeDecision, RunModel, StageResult, StageStatus
from core.orchestrator.steps.review import ReviewStep
from core.policies.engine import PolicyEvaluation, PolicyFinding


class _Runtime:
    def run_worker(self, *args: Any, **kwargs: Any) -> StageResult:
        raise AssertionError("worker should not run when hard policy blocks")


class _GitHubClient:
    comments: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args
        del kwargs

    def get_pull_request(self, repo: str, pull_number: int) -> dict:
        return {
            "html_url": f"https://github.com/{repo}/pull/{pull_number}",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
        }

    def list_pull_request_files(self, repo: str, pull_number: int) -> list[dict]:
        del repo
        del pull_number
        return [{"filename": "infra/auth.py", "status": "modified", "patch": "+x"}]

    def comment_on_pull_request(self, *, repo: str, pull_number: int, body: str) -> dict:
        self.comments.append({"repo": repo, "pull_number": pull_number, "body": body})
        return {"id": 1}

    def close(self) -> None:
        return None


def test_review_step_hard_policy_blocks_and_comments(monkeypatch) -> None:
    _GitHubClient.comments = []
    monkeypatch.setattr(review_steps, "GitHubClient", _GitHubClient)
    monkeypatch.setattr(
        review_steps,
        "evaluate_review_policy",
        lambda _context: PolicyEvaluation(
            decision=MergeDecision(allowed=False, reason="Policy blocked", blocking_reasons=["risk_high"]),
            public_findings=[
                PolicyFinding(
                    reason="This change needs human review.",
                    suggested_fix="Ask an owner to review the behavior.",
                    internal_code="risk_high",
                )
            ],
        ),
    )

    result = ReviewStep().execute(
        {"repository": "acme/repo", "pull_request_number": 4},
        RunModel(state="REVIEW_PENDING", repository="acme/repo", pull_request_number=4),
        _Runtime(),
    )

    assert result.status == StageStatus.BLOCKED
    assert result.notes["blocking_reasons"] == ["risk_high"]
    assert _GitHubClient.comments
    assert "Ask an owner" in _GitHubClient.comments[0]["body"]


def test_review_step_llm_medium_risk_requests_slack_review() -> None:
    result = StageResult(
        stage="review",
        status=StageStatus.OK,
        outputs={
            "llm_review": {
                "merge_risk": "medium",
                "confidence": "high",
                "summary": "Needs review.",
                "blocking_findings": [
                    {
                        "severity": "medium",
                        "summary": "Behavior may be incomplete.",
                        "suggested_fix": "Confirm the edge case.",
                    }
                ],
            }
        },
    )
    context: dict[str, Any] = {
        "repository": "acme/repo",
        "pull_request_number": 4,
        "policy_decision": {"allowed": True, "reason": "ok", "blocking_reasons": []},
    }

    transitions = ReviewStep().after(result, context, RunModel(state="REVIEW_PENDING"))

    assert transitions == []
    assert result.status == StageStatus.NEEDS_REVIEW
    assert result.notes["review_request_kind"] == "llm_soft_gate"
    assert context["merge_risk"] == "medium"


def test_review_step_llm_low_risk_can_merge() -> None:
    result = StageResult(
        stage="review",
        status=StageStatus.OK,
        outputs={"llm_review": {"merge_risk": "low", "confidence": "high", "blocking_findings": []}},
    )
    context = {"policy_decision": {"allowed": True, "reason": "ok", "blocking_reasons": []}}

    transitions = ReviewStep().after(result, context, RunModel(state="REVIEW_PENDING"))

    assert transitions == [("READY_TO_MERGE", "Review and policy checks passed")]
