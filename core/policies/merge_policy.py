def can_merge(all_checks_green: bool, risk_level: str) -> bool:
    if not all_checks_green:
        return False
    return risk_level != "high"
