import logging
import os
import yaml
from pathlib import Path
from copy import deepcopy

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "llm_models.yaml"

class ModelRegistry:
    """Loads model definitions, limits, and endpoints from YAML configuration."""

    def __init__(self, path: str | None = None):
        resolved = path or os.getenv("LLM_MODELS_CONFIG_PATH") or str(_DEFAULT_CONFIG_PATH)
        try:
            with open(Path(resolved)) as f:
                self.config = yaml.safe_load(f)
        except Exception as exc:
            logger.error(
                "llm model registry load failed",
                extra={
                    "event": "config_load_failed",
                    "config": "llm_models",
                    "path": resolved,
                    "error": exc.__class__.__name__,
                },
            )
            raise

        providers = self.config.get("providers", {}) if isinstance(self.config, dict) else {}
        model_count = sum(len(p.get("models", {})) for p in providers.values() if isinstance(p, dict))
        logger.info(
            "llm model registry loaded",
            extra={
                "event": "config_loaded",
                "config": "llm_models",
                "path": resolved,
                "provider_count": len(providers),
                "model_count": model_count,
            },
        )

    def get_model(self, provider: str, model_name: str) -> dict:
        """
        Resolve one model alias with provider defaults and model overrides.

        Args:
            provider: Provider key from the registry.
            model_name: Model alias under the provider.

        Returns:
            Merged model configuration used by clients and limiters.
        """

        provider_cfg = deepcopy(
            self.config["providers"][provider]
        )

        model_cfg = provider_cfg["models"][
            model_name
        ]

        limits = provider_cfg.get("limits", {}).copy()
        limits.update(model_cfg.get("limits", {}))

        return {
            "provider": provider,
            "model_name": model_name,
            "model": model_cfg["model"],
            "endpoint": provider_cfg.get(
                "endpoint"
            ),
            "limits": limits,
        }

    def providers(self):
        """
        Return configured provider entries from the model registry.

        Returns:
            Mapping of provider names to provider configuration dictionaries.
        """

        return self.config["providers"]

registry = ModelRegistry()
