import os

def resolve_github_token(explicit_token: str | None = None) -> str:
    token = explicit_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        raise ValueError("Missing GitHub token. Set GITHUB_TOKEN (or GH_TOKEN).")
    return token

def resolve_optional_github_token(explicit_token: str | None = None) -> str | None:
    return explicit_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
