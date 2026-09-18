from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/app/core/config.py -> repo root is four levels up.
REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    # extra="ignore" — pydantic-settings' own default is "forbid", which
    # made this crash unconditionally (ValidationError at import time,
    # taking every route/test module down with it) the moment `.env` held
    # any var this class doesn't declare a field for — e.g. a provider key
    # for a branch/feature not present here. A settings class reading a
    # shared local .env across branches/environments should tolerate that,
    # not hard-fail the whole app over one line it doesn't recognize.
    # (Consolidated with what used to be a separate, legacy `class Config:
    # env_file = ".env"` below — Pydantic v2 refuses to have both a
    # `Config` inner class and `model_config` defined on the same model.)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # Phase 03 strangler-migration flag — see
    # app/services/execution_profile_service.py's
    # require_active_profile_for_runtime_start and
    # app/api/routes/implementation_runs.py's start_implementation_run.
    # Defaults to False so every existing project (seeded or real, none of
    # which has a ProjectExecutionProfile yet) keeps starting Implementation
    # Agent runs exactly as it always has — flip to True only once the
    # ProjectExecutionProfile propose/approve flow has been evaluated for a
    # given deployment and every active project has an approved profile.
    REQUIRE_EXECUTION_PROFILE_FOR_CODING_RUNTIME: bool = False

    # Phase 05 strangler-migration flag — see app/model_gateway/__init__.py
    # and app/services/ai_generation.py's generate()/generate_raw_text().
    # Defaults to "legacy" so every existing call keeps going through the
    # exact, unmodified provider-priority logic that already exists in
    # ai_generation.py (get_active_provider, _generate_with_*) — nothing
    # about the default request path changes. Set to "gateway" only once
    # the new ModelGateway/ModelPolicy/model-alias architecture has been
    # evaluated for a given deployment.
    MODEL_GATEWAY_MODE: Literal["legacy", "gateway"] = "legacy"
    # Which concrete ModelGateway adapter backs the "gateway" mode above —
    # irrelevant while MODEL_GATEWAY_MODE is "legacy". "fake" is the safe
    # default (never makes a network call) so an operator must explicitly
    # opt into a real backend, not fall into one by omission.
    MODEL_GATEWAY_BACKEND: Literal["litellm_proxy", "openrouter", "ollama", "fake"] = "fake"
    LITELLM_PROXY_BASE_URL: str = "http://localhost:4000"
    LITELLM_PROXY_API_KEY: str | None = None
    # How long a cached provider-health result stays valid — see
    # app/model_gateway/health.py's ProviderHealthCache. This is exactly
    # the mechanism that removes a live connectivity check from every
    # single request's hot path in the new gateway architecture.
    MODEL_GATEWAY_HEALTH_CACHE_TTL_SECONDS: float = 30.0

    # Phase 06 strangler-migration flag — see
    # app/services/agent_jobs/__init__.py. Defaults to "inline": every
    # AgentJob runs synchronously, in-process, inside the request that
    # submitted it (see InlineJobDispatcher) — this IS today's existing
    # synchronous agent-run behavior, now reachable through the new
    # AgentJob interface without changing what actually happens. Set to
    # "celery" only once a real Celery worker + Redis broker have been
    # provisioned for a given deployment.
    AGENT_JOB_DISPATCHER_MODE: Literal["inline", "celery"] = "inline"
    # Irrelevant while AGENT_JOB_DISPATCHER_MODE is "inline". No default
    # pointing at a real Redis instance — an operator must set this
    # explicitly before "celery" mode can even construct a client (see
    # app/services/agent_jobs/celery_dispatcher.py).
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND_URL: str | None = None
    # How long a job may go without a heartbeat while
    # PREPARING/RUNNING before app/services/agent_jobs/job_service.py's
    # find_stale_jobs considers it STALE.
    AGENT_JOB_STALE_THRESHOLD_SECONDS: float = 300.0

    # --- Phase 07: runtime security / credential broker -----------------------------
    #
    # OIDC — see app/runtime_security/oidc_provider.py. Both OIDC_ISSUER
    # and OIDC_AUDIENCE must be set together for OIDCIdentityProvider to
    # construct at all (fails fast, not per-request — see that module).
    OIDC_ISSUER: str | None = None
    OIDC_AUDIENCE: str | None = None
    OIDC_JWKS_URI: str | None = None  # optional override; defaults to '{issuer}/.well-known/jwks.json'
    # Corporate Microsoft Entra ID — see app/runtime_security/entra_id.py.
    # A thin preset over the OIDC settings above; set these INSTEAD OF
    # OIDC_ISSUER/OIDC_AUDIENCE when the identity provider is Entra ID.
    ENTRA_TENANT_ID: str | None = None
    ENTRA_CLIENT_ID: str | None = None
    # Local-development identity adapter — see
    # app/runtime_security/local_dev_provider.py's own module docstring
    # for the two-flag gate this is one half of (ENVIRONMENT=="local" is
    # the other, checked at construction time). Defaults False so a
    # misconfigured ENVIRONMENT value alone can never enable this path.
    ALLOW_LOCAL_DEV_AUTH: bool = False
    # Strangler-migration flag for server-side identity derivation on the
    # routes that have adopted app/runtime_security/dependencies.py's
    # get_current_actor — see that module's own docstring. Defaults False
    # so every existing route's actor-id-in-request-body contract (Phase
    # 00 baseline section 10) is completely unaffected until a deployment
    # explicitly opts in per-route.
    REQUIRE_SERVER_SIDE_IDENTITY: bool = False

    # GitHub App — see app/runtime_security/credential_broker.py's
    # preference for short-lived installation tokens over the long-lived
    # PAT app/services/github_integration.py already supports (Phase 00
    # baseline). All three must be set for the broker to attempt an App
    # token exchange; otherwise it falls back to the existing PAT path.
    GITHUB_APP_ID: str | None = None
    GITHUB_APP_PRIVATE_KEY: str | None = None
    GITHUB_APP_INSTALLATION_ID: str | None = None

    # THE master switch this phase's "Keep all external coding runtimes
    # disabled until this security gate passes" requirement compiles down
    # to — see app/runtime_security/security_gate.py. Defaults False:
    # Settings.AGENT_JOB_DISPATCHER_MODE="celery" (Phase 06) is refused at
    # construction time unless this is also explicitly True AND the
    # security gate's own self-check passes.
    EXTERNAL_CODING_RUNTIMES_ENABLED: bool = False

    # Registers the OpenCode company-managed sandbox (apps/runner,
    # OpenCodeRuntimeAdapter — Phase 08) as an AVAILABLE runtime. Deliberately
    # a separate flag from EXTERNAL_CODING_RUNTIMES_ENABLED: that flag is
    # this codebase's existing security gate for the Phase 06/07
    # Celery-dispatched execution path (app/runtime_security/security_gate.py);
    # this one is apps/runner's own, independent feature flag (mirrored
    # verbatim as OPENCODE_RUNTIME_ENABLED in apps/runner/src/config.py),
    # since apps/runner is a standalone TypeScript service this Python
    # process does not import or execute — enabling one does not enable
    # the other. Both must be true, plus RUNNER_SHARED_SIGNING_SECRET and
    # MODEL_GATEWAY_BASE_URL configured on apps/runner's own side, before
    # any real OpenCode session can run. Defaults False.
    OPENCODE_RUNTIME_ENABLED: bool = False

    # Phase 10's strangler flag: gates POST /implementation-runs/{id}/
    # create-pull-request-v2 (app/services/story_git_pr_flow.py) — a real,
    # additive alternative to create_pull_request's existing one-commit-
    # per-file GitHub Contents API writes, using the Git Data API instead
    # for one real atomic commit, plus an idempotent "update the existing
    # open PR" check create_pull_request doesn't have. The existing
    # endpoint is completely untouched and stays the default regardless of
    # this flag. Defaults False until the v2 path has real usage evidence
    # (Phase 18's own evaluation/canary-rollout requirement) to retire the
    # old path on.
    STORY_GIT_PR_FLOW_V2_ENABLED: bool = False

    # Phase 13's own "keep the bridge feature disabled by default"
    # requirement. Gates every app/api/routes/developer_bridge.py endpoint —
    # while False, all of them return 403 rather than silently no-op, so a
    # misconfigured deployment fails loudly instead of pretending the
    # bridge is unavailable for some other reason.
    DEVELOPER_BRIDGE_ENABLED: bool = False

    # Phase 16's own "enable only this target agent through its runtime
    # configuration" requirement. "disabled" (default): pure legacy
    # behavior, app/services/agent_migration_shadow.py is never called.
    # "shadow": both paths run (see that module's docstring); the legacy
    # result is still what's persisted, but a comparison row is recorded.
    # "enabled": a future cutover value — no caller currently checks for
    # it, since this phase does not wire either mode into the live
    # POST /agent-runs route (disclosed, deferred — see
    # docs/architecture/agent-migration-requirement-intake.md).
    REQUIREMENT_INTAKE_AGENT_V2_MODE: str = "disabled"

    # Phase 17's RuntimeCostRouter — nothing calls it from a live path in
    # this phase (see app/services/runtime_cost_router.py's own module
    # docstring); this flag exists for a future caller to check before
    # doing so. Defaults False.
    RUNTIME_COST_ROUTER_ENABLED: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
