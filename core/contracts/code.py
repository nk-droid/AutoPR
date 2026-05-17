import uuid
from typing import Dict, List
from pydantic import BaseModel, Field

class CodeStep(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    objective: str = Field(..., description="A clear and concise description of the objective of this code step.")
    files: List[str] = Field(..., description="List of file paths that this code step will modify.")
    tests: List[str] = Field(..., description="List of test cases that should be used to validate the changes made in this code step.")

class CodeOutput(BaseModel):
    files_map: Dict[str, str] = Field(default_factory=dict, description="Map of changed file path to full resulting file content.")
    tests_map: Dict[str, str] = Field(default_factory=dict, description="Map of test file path to its content.")
