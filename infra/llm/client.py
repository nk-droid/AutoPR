import os
import requests
from typing import List, Optional

from langchain_classic.llms.base import LLM
from langchain_core.prompts import PromptTemplate

from dotenv import load_dotenv

load_dotenv()

DEFAULT_OLLAMA_ENDPOINT = "https://anaerobic-share-blandness.ngrok-free.dev"
DEFAULT_OLLAMA_MODEL = "deepseek-coder-v2:latest"

def resolve_ollama_endpoint() -> str:
    endpoint = (
        os.getenv("AUTOPR_LLM_ENDPOINT") or
        os.getenv("OLLAMA_ENDPOINT") or
        os.getenv("OLLAMA_BASE_URL") or
        DEFAULT_OLLAMA_ENDPOINT
    )
    if endpoint is None:
        return DEFAULT_OLLAMA_ENDPOINT
    return endpoint

def resolve_ollama_model() -> str:
    model = (
        os.getenv("AUTOPR_LLM_MODEL") or
        os.getenv("OLLAMA_MODEL") or
        os.getenv("OLLAMA_LLM_MODEL") or
        DEFAULT_OLLAMA_MODEL
    )
    if model is None:
        return DEFAULT_OLLAMA_MODEL
    return model

def create_prompt(template: str, inputs: list[str]) -> PromptTemplate:
    return PromptTemplate(template=template, input_variables=inputs)

class OllamaNgrokLLM(LLM):
    endpoint: str = DEFAULT_OLLAMA_ENDPOINT
    model: str = DEFAULT_OLLAMA_MODEL
    timeout_seconds: int = 300

    @property
    def _llm_type(self) -> str:
        return "ollama-http"

    @property
    def _identifying_params(self) -> dict:
        return {"endpoint": self.endpoint, "model": self.model}

    def _call(self, prompt: str, stop: Optional[List[str]] = None) -> str:
        _ = stop
        response = requests.post(
            f"{self.endpoint}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["response"]

def create_client(
    *,
    endpoint: str | None = None,
    model: str | None = None,
    timeout_seconds: int = 300,
) -> OllamaNgrokLLM:
    resolved_endpoint = endpoint or resolve_ollama_endpoint() or DEFAULT_OLLAMA_ENDPOINT
    resolved_model = model or resolve_ollama_model() or DEFAULT_OLLAMA_MODEL
    return OllamaNgrokLLM(
        endpoint=resolved_endpoint,
        model=resolved_model,
        timeout_seconds=int(timeout_seconds),
    )
