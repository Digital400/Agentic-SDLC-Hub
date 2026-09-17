"""ModelGateway — the interface every provider adapter implements, and the
request/response shapes that cross it. See
app/model_gateway/__init__.py's module docstring for the package's status
and the strangler-migration flag that gates it.

Business agents (implementation_agent.py, testing_agent.py,
pr_review_agent.py, validator_agent.py, artifact_summary.py) never import
this module directly — they keep calling
app/services/ai_generation.py's generate()/generate_raw_text(), exactly as
before (Phase 00 baseline section 1). ModelGateway is consumed by
ai_generation.py itself (only when Settings.MODEL_GATEWAY_MODE ==
"gateway") and by app/model_gateway/router.py.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.cost import Cost


class ModelGatewayError(Exception):
    """Raised when a ModelGateway adapter's own call fails — auth, rate
    limit, network, timeout. Mirrors app.services.ai_generation.
    AIGenerationError's role/message-safety contract exactly (never
    include a caller-supplied secret in the message)."""


class ModelGatewayTimeoutError(ModelGatewayError):
    """A more specific failure a router can treat differently from a
    generic error — e.g. worth retrying against a fallback alias, whereas
    an auth failure on the SAME alias never will succeed on retry."""


@dataclass
class GatewayRequest:
    """What a caller asks a ModelGateway for. `alias` is the ONLY way a
    caller names "which model" — never a literal provider model string
    (that resolution is the adapter's own job, via its ALIAS_MODEL_MAP)."""

    alias: ModelAlias
    system_prompt: str
    user_content: str
    output_token_budget: int
    temperature: float | None = None
    structured_output_required: bool = False
    timeout_seconds: float | None = None


@dataclass
class GatewayResponse:
    """What a ModelGateway hands back — carries both what was ASKED for
    and what actually SERVED the request (requested_* vs actual_*), since
    a fallback chain (see router.py) or a provider-side model substitution
    can make the two differ; recording both is this phase's explicit
    "record requested and actual provider/model" requirement.
    """

    content_markdown: str
    needs_clarification: bool
    clarification_questions: list[str]

    requested_alias: ModelAlias
    requested_provider: str
    requested_model: str
    actual_provider: str
    actual_model: str

    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_tokens: int
    cost: Cost

    used_mock: bool = False
    latency_seconds: float = 0.0


@dataclass
class ProviderHealth:
    provider: str
    healthy: bool
    checked_at: datetime
    detail: str | None = None


class ModelGateway(abc.ABC):
    """One provider adapter. Every concrete adapter (LegacyModelGateway,
    LiteLLMProxyGateway, OpenRouterGateway, LocalOllamaGateway,
    FakeModelGateway) implements exactly these two methods — no adapter
    exposes anything provider-SDK-shaped beyond this interface, which is
    what makes "business agents must not call provider SDKs directly"
    structural rather than a convention: there is nothing provider-shaped
    to call even if someone tried.
    """

    name: str

    @abc.abstractmethod
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """Raises ModelGatewayError (or ModelGatewayTimeoutError) on
        failure — never returns a partial/malformed response."""

    @abc.abstractmethod
    def check_health(self) -> ProviderHealth:
        """A single, real (not cached) health probe — callers should go
        through app/model_gateway/health.py's ProviderHealthCache instead
        of calling this directly on every request; see that module's
        docstring for why (this phase's "remove connectivity checks from
        the request hot path" requirement)."""
