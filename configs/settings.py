import os
from pydantic import BaseModel


class DatabaseSettings(BaseModel):
    url: str = os.getenv(
        "AUTOPR_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
    )
    async_url: str = os.getenv(
        "AUTOPR_DATABASE_ASYNC_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
    )

    pool_size: int = 10
    max_overflow: int = 5
    echo: bool = False


class Settings(BaseModel):
    app_name: str = "AutoPR"
    max_autonomous_loops: int = 3

    database: DatabaseSettings = DatabaseSettings()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
