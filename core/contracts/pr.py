from pydantic import BaseModel, Field


class PROpenRequest(BaseModel):
    repository: str = Field(..., description="Repository in owner/repo format.")
    title: str = Field(..., description="Pull request title.")
    head: str = Field(..., description="Head branch name.")
    head_repo: str | None = Field(
        default=None,
        description="Optional source repository name for cross-repository PRs within the same org.",
    )
    base: str = Field(..., description="Base branch name.")
    body: str = Field(default="", description="Pull request body.")
    draft: bool = Field(default=False, description="Whether PR should be opened as draft.")


class PROpenOutput(BaseModel):
    request: PROpenRequest | None = Field(
        default=None, description="PR request payload used for open operation."
    )
    pull_request_number: int | None = Field(
        default=None, description="Opened pull request number if available."
    )
    pull_request_url: str = Field(default="", description="Opened pull request URL if available.")
    summary: str = Field(default="", description="High-level summary of PR stage result.")
