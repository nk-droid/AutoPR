from typing import Any, TypeVar

from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from infra.llm.client import LLMClient, create_client
from infra.llm.callbacks import LLMMetricsCallbackHandler
from observability.tracing import traced, llm_chain_attrs
from observability.metrics import LLM_PARSE_ERRORS_TOTAL

ModelT = TypeVar("ModelT", bound=BaseModel)

def build_chain_parser(
    *,
    template: str,
    input_vars: list[str],
    output_model: type[ModelT],
    client: LLMClient,
) -> tuple[Any, PydanticOutputParser]:
    parser = PydanticOutputParser(pydantic_object=output_model)
    prompt = PromptTemplate(template=template, input_variables=input_vars)
    chain = prompt | client.client | parser
    return chain, parser

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
    client = client or create_client()
    chain, parser = build_chain_parser(
        template=template,
        input_vars=input_vars,
        output_model=output_model,
        client=client,
    )

    invoke_payload = dict(variables)
    if include_format_instructions:
        invoke_payload["format_instructions"] = parser.get_format_instructions()

    handler = LLMMetricsCallbackHandler(
        provider=client.provider,
        model=client.model,
        agent=agent,
        node=node,
        output_model=output_model.__name__,
    )

    try:
        response = chain.invoke(invoke_payload, config={"callbacks": [handler]})
    except Exception:
        raise  # handler.on_llm_error already fired for LLM-layer errors

    try:
        if isinstance(response, output_model):
            return response
        return output_model.model_validate(response)
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
