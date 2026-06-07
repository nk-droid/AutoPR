PRICING: dict[str, tuple[float, float]] = {
    "qwen3-coder:480b-cloud": (0.0, 0.0),
}

def estimate_cost_usd(model_name: str, in_tokens: int, out_tokens: int) -> float:
    rates = PRICING.get(model_name)
    if rates is None:
        raise ValueError(f"Unknown model: {model_name}")
    
    in_rate, out_rate = rates
    return in_tokens * in_rate + out_tokens * out_rate