from pydantic import BaseModel


class RunContext(BaseModel):
    run_id: str
    issue_number: int
    repository: str
