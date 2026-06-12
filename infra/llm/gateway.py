import asyncio
import logging
import threading

from infra.llm.registry import registry
from infra.llm.client import create_client
from infra.llm.rate_limit import build_limiter

logger = logging.getLogger(__name__)


class LLMGateway:
    """Routes model calls through per-model rate limits and concurrency caps.

    Limits are global (shared via Redis) when AUTOPR_REDIS_URL is set, otherwise
    enforced per worker. Keyed by (provider, underlying model id) so it can be
    driven straight from ``LLMClient.model``.
    """

    def __init__(self):
        self.limiters: dict[tuple[str, str], object] = {}
        self._alias: dict[tuple[str, str], str] = {}
        self._clients: dict[tuple[str, str], object] = {}
        self._lock = threading.Lock()
        self._initialize_limits()

    def _initialize_limits(self) -> None:
        """Create per-model limiters from the shared model registry."""

        for provider_name, provider_cfg in registry.providers().items():
            for model_alias in provider_cfg["models"]:
                cfg = registry.get_model(provider_name, model_alias)
                key = (provider_name, cfg["model"])
                self.limiters[key] = build_limiter(
                    key_prefix=f"autopr:llm:{provider_name}:{cfg['model']}",
                    rpm=cfg["limits"]["max_rpm"],
                    max_concurrent=cfg["limits"]["max_concurrent"],
                )
                self._alias[key] = model_alias

    def _get_client(self, key: tuple[str, str]):
        """
        Lazily create and cache the client for a provider model key.

        Args:
            key: Provider name and underlying model id pair.

        Returns:
            Cached LLM client for the requested model key.
        """

        client = self._clients.get(key)
        if client is None:
            with self._lock:
                client = self._clients.get(key)
                if client is None:
                    provider, _model = key
                    client = create_client(provider=provider, model_name=self._alias[key])
                    self._clients[key] = client
        return client

    def invoke(self, *, provider: str, model: str, prompt, config: dict | None = None):
        """
        Invoke an LLM through configured rate limits and concurrency controls.

        Args:
            provider: Provider name from the model registry.
            model: Underlying provider model id.
            prompt: Prompt value passed to the LangChain client.
            config: Optional LangChain invocation configuration.

        Returns:
            Raw client response from the selected model.
        """

        key = (provider, model)
        limiter = self.limiters.get(key)
        if limiter is None:
            logger.warning(
                "unknown model requested from gateway",
                extra={"event": "llm_unknown_model", "provider": provider, "model": model},
            )
            raise KeyError(f"Unknown model for gateway: {provider}/{model}")

        logger.debug(
            "llm request",
            extra={"event": "llm_request", "provider": provider, "model": model},
        )
        token = limiter.acquire()
        try:
            client = self._get_client(key)
            return client.client.invoke(prompt, config=config or {})
        except Exception as exc:
            logger.warning(
                "llm call failed",
                extra={
                    "event": "llm_call_failed",
                    "provider": provider,
                    "model": model,
                    "error": exc.__class__.__name__,
                },
            )
            raise
        finally:
            limiter.release(token)

    async def ainvoke(self, *, provider: str, model: str, prompt, config: dict | None = None):
        """
        Invoke an LLM asynchronously through the same gateway controls.

        Args:
            provider: Provider name from the model registry.
            model: Underlying provider model id.
            prompt: Prompt value passed to the LangChain client.
            config: Optional LangChain invocation configuration.

        Returns:
            Raw client response from the selected model.
        """

        return await asyncio.to_thread(
            self.invoke, provider=provider, model=model, prompt=prompt, config=config
        )


gateway = LLMGateway()
