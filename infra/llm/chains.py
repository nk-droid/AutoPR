from typing import Any, TypeVar

from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from infra.llm.client import LLMClient, create_client
from infra.llm.callbacks import LLMMetricsCallbackHandler
from infra.llm.gateway import gateway
from observability.tracing import traced, llm_chain_attrs
from observability.metrics import LLM_PARSE_ERRORS_TOTAL

ModelT = TypeVar("ModelT", bound=BaseModel)

@traced("llm.invoke_chain", attributes=llm_chain_attrs)
def invoke_chain(
    *,
    template: str,
    input_vars: list[str],
    output_model: type[ModelT],
    variables: dict[str, Any],
    agent: str,
    node: str,
    client: LLMClient | None = None,
    include_format_instructions: bool = False,
) -> ModelT:
    """
    Invoke an LLM prompt and parse the response into a Pydantic model.

    Args:
        template: Prompt template string passed to LangChain.
        input_vars: Prompt variable names expected by the template.
        output_model: Pydantic model required from the LLM response.
        variables: Runtime values bound into the prompt template.
        agent: Agent name used for metrics and tracing labels.
        node: Node name used for metrics and tracing labels.
        client: Optional LLM client override for tests or custom routing.
        include_format_instructions: Whether parser instructions enter the prompt.

    Returns:
        Parsed and validated output model instance.
    """

    client = client or create_client()

    parser = PydanticOutputParser(pydantic_object=output_model)
    prompt = PromptTemplate(template=template, input_variables=input_vars)

    invoke_payload = dict(variables)
    if include_format_instructions:
        invoke_payload["format_instructions"] = parser.get_format_instructions()

    prompt_value = prompt.invoke(invoke_payload)

    handler = LLMMetricsCallbackHandler(
        provider=client.provider,
        model=client.model,
        agent=agent,
        node=node,
        output_model=output_model.__name__,
    )

    # Model call goes through the gateway for per-model rate limiting + concurrency.
    response = gateway.invoke(
        provider=client.provider,
        model=client.model,
        prompt=prompt_value,
        config={"callbacks": [handler]},
    )

    try:
        parsed = parser.invoke(response)
        if isinstance(parsed, output_model):
            return parsed
        return output_model.model_validate(parsed)
    except Exception:
        LLM_PARSE_ERRORS_TOTAL.add(
            1,
            {
                "provider": client.provider,
                "model": client.model,
                "agent": agent,
                "node": node,
                "output_model": output_model.__name__,
            },
        )
        raise
