from core.orchestrator.models import StageResult, StageStatus
from core.policies.engine import PolicyConfig, evaluate_review_policy


def test_review_policy_blocks_high_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.policies.engine.load_policy_config",
        lambda: PolicyConfig(block_high_risk_automerge=True),
    )

    result = evaluate_review_policy({"risk": {"level": "high"}})

    assert result.decision.allowed is False
    assert "risk_high" in result.decision.blocking_reasons
    assert result.public_findings[0].suggested_fix


def test_review_policy_blocks_sensitive_path(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.policies.engine.load_policy_config",
        lambda: PolicyConfig(sensitive_path_patterns=["infra/**"]),
    )

    result = evaluate_review_policy({"changed_files": [{"filename": "infra/auth.py"}]})

    assert result.decision.allowed is False
    assert "sensitive_path_changed" in result.decision.blocking_reasons


def test_review_policy_blocks_unready_qa(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.policies.engine.load_policy_config",
        lambda: PolicyConfig(block_high_risk_automerge=False),
    )

    result = evaluate_review_policy(
        {"_stage_results": {"qa": StageResult(stage="qa", status=StageStatus.BLOCKED)}}
    )

    assert result.decision.allowed is False
    assert "qa_not_ready" in result.decision.blocking_reasons
