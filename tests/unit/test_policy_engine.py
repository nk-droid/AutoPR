import pytest

from core.contracts.enums import RiskLevel
from core.policies.merge_policy import can_merge


def test_can_merge_requires_green_checks() -> None:
    assert can_merge(True, RiskLevel.LOW) is True
    assert can_merge(False, RiskLevel.LOW) is False


def test_can_merge_blocks_high_risk_even_when_checks_green() -> None:
    assert can_merge(True, RiskLevel.HIGH) is False
    assert can_merge(True, "high") is False
    with pytest.raises(ValueError):
        can_merge(True, "unknown")
