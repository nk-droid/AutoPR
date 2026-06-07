from typing import NamedTuple

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from dotenv import load_dotenv
load_dotenv()

PROVIDER_MAPPING = {
    "openai": ChatOpenAI,
    "ollama": ChatOllama,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
}

class LLMClient(NamedTuple):
    client: object
    provider: str
    model: str

def create_client(
    *,
    provider: str = "ollama",
    model: str = "qwen3-coder:480b-cloud",
    endpoint: str | None = "http://host.docker.internal:11434",
    timeout_seconds: int = 300,
) -> LLMClient:
    cls = PROVIDER_MAPPING[provider]
    kwargs: dict = {"model": model}
    if provider == "ollama" and endpoint:
        kwargs["base_url"] = endpoint
    return LLMClient(client=cls(**kwargs), provider=provider, model=model)

if __name__ == "__main__":
    from langchain_core.prompts import PromptTemplate
    from langchain_classic.output_parsers import PydanticOutputParser
    from pydantic import Field, BaseModel

    class Summary(BaseModel):
        summary: str = Field(..., description="Paragraph of the text")

    template = """
Give a paragraph on of the following topic:
{text}

Return the answer the following format:
{format_instructions}
"""

    parser = PydanticOutputParser(pydantic_object=Summary)
    prompt = PromptTemplate(
        template=template,
        input_variables=["text"],
        partial_variables={"format_instructions": parser.get_format_instructions()}
    )

    llm = create_client()
    chain = prompt | llm.client
    full_chain = chain | parser

    result = chain.invoke({
        "text": "Responsible AI"
    })

    print(dir(result))
    print(result.usage_metadata)
