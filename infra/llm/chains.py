from typing import Any, TypeVar

from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from infra.llm.client import create_client

ModelT = TypeVar("ModelT", bound=BaseModel)

def build_chain_parser(
    *,
    template: str,
    input_vars: list[str],
    output_model: type[ModelT],
    client: Any | None = None,
) -> tuple[Any, PydanticOutputParser]:
    parser = PydanticOutputParser(pydantic_object=output_model)
    prompt = PromptTemplate(template=template, input_variables=input_vars)
    chain = prompt | (client or create_client()) | parser
    return chain, parser

def invoke_chain(
    *,
    template: str,
    input_vars: list[str],
    output_model: type[ModelT],
    variables: dict[str, Any],
    client: Any | None = None,
    include_format_instructions: bool = False,
) -> ModelT:
    chain, parser = build_chain_parser(
        template=template,
        input_vars=input_vars,
        output_model=output_model,
        client=client,
    )

    invoke_payload = dict(variables)
    if include_format_instructions:
        invoke_payload["format_instructions"] = parser.get_format_instructions()

    response = chain.invoke(invoke_payload)
    if isinstance(response, output_model):
        return response
    
    return output_model.model_validate(response)