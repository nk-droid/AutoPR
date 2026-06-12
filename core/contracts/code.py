import uuid
from typing import Dict, List
from pydantic import BaseModel, Field, model_validator


class CodeStep(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    objective: str = Field(
        ..., description="A clear and concise description of the objective of this code step."
    )
    files: List[str] = Field(..., description="List of file paths that this code step will modify.")
    tests: List[str] = Field(
        ...,
        description="List of test cases that should be used to validate the changes made in this code step.",
    )


class CodeOutput(BaseModel):
    files_map: Dict[str, str] = Field(
        ..., description="Map of changed file path to full resulting file content."
    )
    tests_map: Dict[str, str] = Field(..., description="Map of test file path to its content.")

    @model_validator(mode="after")
    def validate_files_and_tests(self):
        if not self.files_map and not self.tests_map:
            raise ValueError("At least one of files_map or tests_map must be provided.")
        return self
