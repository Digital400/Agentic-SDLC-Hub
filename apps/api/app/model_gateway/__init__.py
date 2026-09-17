"""ModelGateway — Phase 05's provider-abstraction boundary.

STATUS: additive, strangler-migration architecture, same posture as
app/agent_runtime (Phase 01), app/services/execution_profile_service.py's
require_active_profile_for_runtime_start gate (Phase 03), and
app/prompt_compiler (Phase 04) all established before it.
Settings.MODEL_GATEWAY_MODE defaults to "legacy" — app/services/
ai_generation.py's generate()/generate_raw_text() keep using their
existing, UNMODIFIED provider-priority logic by default; nothing about
the default request path changes. Set MODEL_GATEWAY_MODE="gateway" (and
MODEL_GATEWAY_BACKEND to a real adapter) only once this new architecture
has been evaluated for a given deployment.

WHY THIS EXISTS (this phase's requirements, each satisfied by a specific
piece of this package):
  - "Business agents must not call provider SDKs directly" — already true
    (Phase 00 baseline section 1: implementation_agent.py/testing_agent.py/
    pr_review_agent.py/validator_agent.py/artifact_summary.py only ever
    call app.services.ai_generation.generate()/generate_raw_text()) and
    now structurally reinforced: every provider SDK call
    (anthropic/genai/ollama/httpx-to-a-provider) lives behind a
    ModelGateway adapter — see base.py's ModelGateway ABC, and
    tests/test_business_agents_no_direct_sdk_calls.py, which greps every
    business-agent module's source for a provider SDK import.
  - Model aliases (aliases.py's ModelAlias) instead of literal model
    strings scattered across call sites.
  - ModelPolicy (policy.py) — allowed/default/fallback aliases, token/
    call/cost/timeout/temperature ceilings, structured-output requirement,
    data-region restriction, escalation conditions.
  - "Remove model connectivity checks from the request hot path. Use
    cached provider health." — health.py's ProviderHealthCache; every NEW
    adapter's own `cached_health()` method goes through it (the legacy
    path's per-request check is intentionally left as-is — see
    legacy_gateway.py).
  - "Record requested and actual provider/model" + "input, output and
    cached tokens" — base.py's GatewayResponse fields.
  - "Never record an unknown paid cost as zero. Represent unknown cost
    explicitly." — cost.py's Cost type; openrouter_gateway.py/
    litellm_gateway.py are the two adapters that actually exercise the
    "unknown" branch (see their own docstrings for exactly when).
  - "Support company and project budgets" — budget.py's BudgetScope/
    BudgetTracker.
  - Adapters: legacy_gateway.py (LegacyModelGateway), litellm_gateway.py
    (LiteLLMProxyGateway), openrouter_gateway.py (OpenRouterGateway),
    ollama_gateway.py (LocalOllamaGateway), fake_gateway.py
    (FakeModelGateway).
  - router.py's PolicyDrivenRouter — timeout/retry/fallback across a
    policy's default+fallback alias chain.
"""

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import (
    GatewayRequest,
    GatewayResponse,
    ModelGateway,
    ModelGatewayError,
    ModelGatewayTimeoutError,
    ProviderHealth,
)
from app.model_gateway.budget import BudgetExceededError, BudgetScope, BudgetTracker, InMemoryBudgetTracker
from app.model_gateway.cost import Cost
from app.model_gateway.gateway_factory import UnknownGatewayBackendError, select_gateway
from app.model_gateway.health import ProviderHealthCache, get_shared_health_cache
from app.model_gateway.policy import DEFAULT_MODEL_POLICY, EscalationCondition, ModelPolicy
from app.model_gateway.router import PolicyDrivenRouter, PolicyExhaustedError

__all__ = [
    "ModelAlias",
    "GatewayRequest",
    "GatewayResponse",
    "ModelGateway",
    "ModelGatewayError",
    "ModelGatewayTimeoutError",
    "ProviderHealth",
    "BudgetExceededError",
    "BudgetScope",
    "BudgetTracker",
    "InMemoryBudgetTracker",
    "Cost",
    "select_gateway",
    "UnknownGatewayBackendError",
    "ProviderHealthCache",
    "get_shared_health_cache",
    "ModelPolicy",
    "EscalationCondition",
    "DEFAULT_MODEL_POLICY",
    "PolicyDrivenRouter",
    "PolicyExhaustedError",
]
