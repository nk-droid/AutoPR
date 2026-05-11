from pydantic import BaseModel


class AgentOutput(BaseModel):
    status: str
    summary: str
    artifacts: dict = {}
