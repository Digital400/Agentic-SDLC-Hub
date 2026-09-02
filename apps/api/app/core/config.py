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
    # (Google AI Studio issues Gemini API keys with a free tier); if all of
    # those are unset but OPENROUTER_API_KEY is, OpenRouter (openrouter.ai —
    # a single OpenAI-compatible endpoint fronting many providers, including
    # several ":free"-suffixed models with no cost) is used; then NVIDIA's
    # hosted "Build" API (build.nvidia.com, same OpenAI-compatible shape);
    # Ollama (local, no key at all) is the last resort before mock.
    # Priority: Anthropic > Gemini > OpenRouter > NVIDIA > Ollama > mock —
    # both OpenRouter and NVIDIA are placed ahead of Ollama because they're
    # fast hosted calls, not slow local CPU inference; OpenRouter is placed
    # ahead of NVIDIA per this codebase's own observed experience (NVIDIA's
    # hosted endpoint has repeatedly been found to hang rather than error).
    ANTHROPIC_API_KEY: str | None = None
    AI_MODEL: str = "claude-opus-5"
    GEMINI_API_KEY: str | None = None
    # gemini-2.5-flash was Google's default when this was first wired up,
    # but new API keys now get a 404 ("no longer available to new users")
    # on it — Google's own error message names gemini-3.6-flash as the
    # replacement, confirmed against a real key.
    GEMINI_MODEL: str = "gemini-3.6-flash"
    # OpenRouter (https://openrouter.ai) — a single OpenAI-compatible
    # /v1/chat/completions endpoint that routes to many underlying
    # providers/models, several suffixed ":free" with no cost. Get a key
    # from openrouter.ai/keys. OPENROUTER_MODEL defaults to a free-tier
    # model; point it at any model slug from openrouter.ai/models. Which
    # models are free changes over time — check openrouter.ai/models?
    # max_price=0 — and prefer a plain instruct model over a "reasoning"
    # one (a reasoning model can spend the whole output-token budget on
    # hidden reasoning tokens before emitting any real content).
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_MODEL: str = "minimax/minimax-m3:free"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # NVIDIA's hosted "Build" API (https://build.nvidia.com) — issues free
    # API keys for prototyping against a catalog of hosted models via a
    # single OpenAI-compatible endpoint. moonshotai/kimi-k3 is the default
    # (a large MoE model with a 1M context window), but NVIDIA_MODEL can
    # point at any model in their catalog using the same endpoint shape.
    NVIDIA_API_KEY: str | None = None
    NVIDIA_MODEL: str = "moonshotai/kimi-k3"
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    # Ollama local inference (free, no API key needed). Runs at
    # http://localhost:11434 by default.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"
    # Superseded by each WorkflowNode's own output_token_budget (see
    # app/models/workflow.py and app/services/token_budget.py) — every real
    # model call is now capped by the node's budget instead of this global
    # ceiling (workflow_templates.py's own DEFAULT_OUTPUT_TOKEN_BUDGET is
    # the fallback when a template omits outputTokenBudget). No longer read
    # anywhere on the actual call path; kept only so an existing .env
    # setting this doesn't suddenly become an unknown-key error.
    AI_MAX_TOKENS: int = 4096

    # Encrypts the GitHub PAT at rest (see app/core/security.py) — a
    # pragmatic MVP bridge, NOT the vault-based design docs/architecture.md's
    # MCP integrations section actually calls for (no vault exists anywhere
    # in this codebase yet). Must be a valid Fernet key (44 url-safe base64
    # chars) — generate one with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    # Left unset here on purpose: app/core/security.py derives a clearly-
    # marked dev-only fallback when this is empty, so the app stays runnable
    # out of the box, but a real deployment MUST set a real one — a token
    # encrypted under the dev-only key is only as safe as this repo itself.
    GITHUB_TOKEN_ENCRYPTION_KEY: str | None = None

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
