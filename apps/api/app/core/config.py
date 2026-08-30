from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings

# apps/api/app/core/config.py -> repo root is four levels up.
REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    PROJECT_NAME: str = "Agentic SDLC Hub API"
    ENVIRONMENT: str = "local"

    # Comma-separated list of allowed origins for local dev.
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/agentic_sdlc_hub"

    # Directory holding workflow template JSON files (e.g. sdlc-workflow.json).
    # Defaults to the repo-root `workflows/` folder.
    WORKFLOWS_DIR: Path = REPO_ROOT / "workflows"

    # File name (within WORKFLOWS_DIR) of the default workflow template used
    # when a new project doesn't specify one explicitly.
    DEFAULT_WORKFLOW_FILE: str = "sdlc-workflow.json"

    # Real AI generation (see app/services/ai_generation.py). Left unset by
    # default — agent runs fall back to deterministic mock output when no
    # key is configured, so the system stays fully testable without a paid
    # key. Set ANTHROPIC_API_KEY in apps/api/.env to turn on real Claude
    # calls; if that's unset but GEMINI_API_KEY is, Gemini is used instead
    # (Google AI Studio issues Gemini API keys with a free tier) — Claude
    # takes priority when both are set. Mock is the fallback only when
    # neither is configured.
    ANTHROPIC_API_KEY: str | None = None
    AI_MODEL: str = "claude-opus-5"
    GEMINI_API_KEY: str | None = None
    # gemini-2.5-flash was Google's default when this was first wired up,
    # but new API keys now get a 404 ("no longer available to new users")
    # on it — Google's own error message names gemini-3.6-flash as the
    # replacement, confirmed against a real key.
    GEMINI_MODEL: str = "gemini-3.6-flash"
    # Kept well below the ~16k default for a fresh chat response — these are
    # short, structured SDLC stage documents (see packages/prompts), not
    # long-form essays, so a smaller ceiling is a deliberate cost control,
    # not a generic default.
    AI_MAX_TOKENS: int = 4096

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
