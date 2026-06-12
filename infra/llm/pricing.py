import os
import yaml
from pathlib import Path
from typing import Any

_DEFAULT_MODELS_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "llm_models.yaml"
_TOKENS_PER_MILLION = 1_000_000


def load_pricing(path: str | Path | None = None) -> dict[str, tuple[float, float]]:
    """
    Load per-token LLM prices from the shared model configuration file.

    Args:
        path: Optional config path overriding LLM_MODELS_CONFIG_PATH.

    Returns:
        Mapping from provider model id to input and output token rates.
    """

    resolved = Path(path or os.getenv("LLM_MODELS_CONFIG_PATH") or _DEFAULT_MODELS_CONFIG_PATH)
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}

    providers = raw.get("providers") if isinstance(raw, dict) else None
    if not isinstance(providers, dict):
        raise ValueError(f"LLM model config must define a 'providers' mapping: {resolved}")

    pricing: dict[str, tuple[float, float]] = {}
    for provider_name, provider_cfg in providers.items():
        if not isinstance(provider_cfg, dict):
            raise ValueError(f"Provider entry for '{provider_name}' must be a mapping")

        models = provider_cfg.get("models")
        if not isinstance(models, dict):
            raise ValueError(f"Provider '{provider_name}' must define a 'models' mapping")

        for model_alias, model_cfg in models.items():
            model_name = _model_name(provider_name, str(model_alias), model_cfg)
            rates = _pricing_rates(provider_name, str(model_alias), model_cfg)
            pricing[model_name] = (
                _rate_per_token(rates, "input_per_1m_tokens_usd", model_name),
                _rate_per_token(rates, "output_per_1m_tokens_usd", model_name),
            )

    return pricing


def _model_name(provider_name: str, model_alias: str, model_cfg: Any) -> str:
    if not isinstance(model_cfg, dict):
        raise ValueError(f"Model entry for '{provider_name}/{model_alias}' must be a mapping")

    model_name = model_cfg.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError(f"Model entry for '{provider_name}/{model_alias}' must define 'model'")

    return model_name


def _pricing_rates(provider_name: str, model_alias: str, model_cfg: Any) -> dict[str, Any]:
    pricing = model_cfg.get("pricing")
    if not isinstance(pricing, dict):
        raise ValueError(f"Model entry for '{provider_name}/{model_alias}' must define 'pricing'")

    return pricing


def _rate_per_token(rates: dict[str, Any], key: str, model_name: str) -> float:
    try:
        return float(rates[key]) / _TOKENS_PER_MILLION
    except KeyError as exc:
        raise ValueError(f"Pricing entry for '{model_name}' is missing '{key}'") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Pricing entry for '{model_name}' has an invalid '{key}'") from exc


PRICING: dict[str, tuple[float, float]] = load_pricing()


def estimate_cost_usd(model_name: str, in_tokens: int, out_tokens: int) -> float:
    """
    Estimate LLM spend for one request using configured token prices.

    Args:
        model_name: Provider model id used for the request.
        in_tokens: Number of input tokens consumed.
        out_tokens: Number of output tokens generated.

    Returns:
        Estimated request cost in US dollars.
    """

    rates = PRICING.get(model_name)
    if rates is None:
        raise ValueError(f"Unknown model: {model_name}")

    in_rate, out_rate = rates
    return in_tokens * in_rate + out_tokens * out_rate
