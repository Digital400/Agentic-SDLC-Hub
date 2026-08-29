from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Agentic SDLC Hub API"
    ENVIRONMENT: str = "local"

    # Comma-separated list of allowed origins for local dev.
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Placeholder for future use; not wired up yet.
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/agentic_sdlc_hub"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
