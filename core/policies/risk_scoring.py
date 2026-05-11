def score_risk(change_count: int) -> str:
    if change_count < 10:
        return "low"
    if change_count < 50:
        return "medium"
    return "high"
