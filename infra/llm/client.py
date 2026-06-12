import os
from typing import NamedTuple

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from infra.llm.registry import registry

PROVIDER_MAPPING = {
    "openai": ChatOpenAI,
    "ollama": ChatOllama,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
}

_DEFAULT_PROVIDER = "ollama"
_DEFAULT_MODEL_NAME = "qwen3-coder"


class LLMClient(NamedTuple):
    client: object
    provider: str
    model: str  # underlying model id; also the gateway rate-limit routing key


def create_client(
    *,
    provider: str | None = None,
    model_name: str | None = None,
) -> LLMClient:
    provider = provider or os.getenv("AUTOPR_LLM_PROVIDER", _DEFAULT_PROVIDER)
    model_name = model_name or os.getenv("AUTOPR_LLM_MODEL_NAME", _DEFAULT_MODEL_NAME)

    cfg = registry.get_model(provider, model_name)

    kwargs = {"model": cfg["model"]}
    if provider == "ollama" and cfg.get("endpoint"):
        kwargs["base_url"] = cfg["endpoint"]

    client = PROVIDER_MAPPING[provider](**kwargs)

    return LLMClient(
        client=client,
        provider=provider,
        model=cfg["model"],
    )
