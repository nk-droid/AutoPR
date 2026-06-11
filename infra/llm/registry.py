import os
import yaml
from pathlib import Path
from copy import deepcopy

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "llm_models.yaml"

class ModelRegistry:
    """Loads model definitions, limits, and endpoints from YAML configuration."""

    def __init__(self, path: str | None = None):
        resolved = path or os.getenv("LLM_MODELS_CONFIG_PATH") or str(_DEFAULT_CONFIG_PATH)
        with open(Path(resolved)) as f:
            self.config = yaml.safe_load(f)

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
