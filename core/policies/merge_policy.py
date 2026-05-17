from core.contracts.enums import RiskLevel

def can_merge(all_checks_green: bool, risk_level: RiskLevel | str) -> bool:
    if not all_checks_green:
        return False
    return RiskLevel(risk_level) != RiskLevel.HIGH
