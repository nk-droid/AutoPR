import time
from typing import Any
from uuid import UUID

from opentelemetry import trace
from langchain_core.outputs import LLMResult
from langchain_core.callbacks import BaseCallbackHandler

from infra.llm.pricing import estimate_cost_usd
from observability.metrics import (
    LLM_COST_USD_TOTAL,
    LLM_IN_FLIGHT,
    LLM_INPUT_TOKENS_TOTAL,
    LLM_OUTPUT_TOKENS_TOTAL,
    LLM_REQUEST_DURATION_SECONDS,
    LLM_REQUESTS_TOTAL,
    LLM_TOKENS_PER_REQUEST,
    LLM_TOKENS_PER_SECOND,
    configure_metrics,
)

def _extract_usage(response: LLMResult) -> tuple[int, int]:
    in_tokens = out_tokens = 0
    for generation in response.generations:
        for gen in generation:
            msg = getattr(gen, "message", None)
            usage = getattr(msg, "usage_metadata", None) if msg else None
            if usage:
                in_tokens += usage.get("input_tokens", 0)
                out_tokens += usage.get("output_tokens", 0)

    if in_tokens == 0 and out_tokens == 0:
        token_usage = (response.llm_output or {}).get("token_usage", {})
        in_tokens = token_usage.get("prompt_tokens", 0)
        out_tokens = token_usage.get("completion_tokens", 0)

    return in_tokens, out_tokens

class LLMMetricsCallbackHandler(BaseCallbackHandler):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        agent: str,
        node: str,
        output_model: str,
    ) -> None:
        configure_metrics()
        self.provider = provider
        self.model = model
        self.agent = agent
        self.node = node
        self.output_model = output_model
        self._starts: dict[UUID, float] = {}

    @property
    def _attrs(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "agent": self.agent,
            "node": self.node,
        }

    def on_llm_start(
        self,
        serialized: dict,
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any
    ) -> None:
        self._starts[run_id] = time.time()
        LLM_IN_FLIGHT.add(1, {"provider": self.provider, "model": self.model})

    on_chat_model_start = on_llm_start

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any
    ) -> None:
        start_time = self._starts.pop(run_id, time.time())
        duration = time.time() - start_time
        LLM_IN_FLIGHT.add(-1, {"provider": self.provider, "model": self.model})

        in_tokens, out_tokens = _extract_usage(response)
        attrs = self._attrs

        LLM_REQUESTS_TOTAL.add(1, {**attrs, "status": "success"})
        LLM_REQUEST_DURATION_SECONDS.record(duration, {**attrs, "status": "success"})
        LLM_INPUT_TOKENS_TOTAL.add(in_tokens, attrs)
        LLM_OUTPUT_TOKENS_TOTAL.add(out_tokens, attrs)
        if in_tokens:
            LLM_TOKENS_PER_REQUEST.record(
                in_tokens,
                {"provider": self.provider, "model": self.model, "agent": self.agent, "kind": "input"},
            )
        if out_tokens:
            LLM_TOKENS_PER_REQUEST.record(
                out_tokens,
                {"provider": self.provider, "model": self.model, "agent": self.agent, "kind": "output"},
            )
        if duration > 0 and out_tokens:
            LLM_TOKENS_PER_SECOND.record(
                out_tokens / duration, {"provider": self.provider, "model": self.model}
            )

        cost = estimate_cost_usd(self.model, in_tokens, out_tokens)
        if cost:
            LLM_COST_USD_TOTAL.add(cost, attrs)

        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute("gen_ai.system", self.provider)
            span.set_attribute("gen_ai.request.model", self.model)
            span.set_attribute("gen_ai.usage.input_tokens", in_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", out_tokens)
            if cost:
                span.set_attribute("autopr.llm.cost_usd", cost)

    def on_llm_error(
        self,
        error: Exception,
        *,
        run_id: UUID,
        **kwargs: Any
    ) -> None:
        start_time = self._starts.pop(run_id, time.time())
        duration = time.time() - start_time
        LLM_IN_FLIGHT.add(-1, {"provider": self.provider, "model": self.model})

        status = "timeout" if "timeout" in type(error).__name__.lower() else "provider_error"
        attrs = self._attrs
        LLM_REQUESTS_TOTAL.add(1, {**attrs, "status": status})
        LLM_REQUEST_DURATION_SECONDS.record(duration, {**attrs, "status": status})
