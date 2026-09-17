"""LegacyModelGateway — a ModelGateway adapter that delegates to
app/services/ai_generation.py's existing, UNMODIFIED provider-priority
logic (get_active_provider, _generate_with_anthropic/_gemini/_openrouter/
_nvidia/_ollama).

WHY DELEGATION, NOT DUPLICATION: this is what makes "preserve the legacy
provider path behind a feature flag" a structural guarantee rather than a
hope that two copies of the same logic stay in sync. This adapter cannot
drift from actual legacy behavior because it calls the exact same
functions every existing agent run already goes through — it does not
reimplement provider selection, reachability checking, or any
_generate_with_* call. It exists purely to let the LEGACY path be
exercised through the new ModelGateway interface (e.g. by
app/model_gateway/router.py), for anything that wants a uniform interface
without abandoning the existing, still-authoritative behavior.

`alias` is accepted (the interface requires it) but NOT used for model
selection — the legacy path has no alias concept; `requested_model` is
whichever literal model Settings.AI_MODEL/OPENROUTER_MODEL/etc. already
names. requested_provider/actual_provider are always identical on this
adapter (the legacy path has no separate "requested vs. actually served"
distinction — see base.py's GatewayResponse docstring for where that
distinction actually matters, i.e. the NEW gateways' alias resolution and
router.py's fallback chain).
"""

from __future__ import annotations

import time

from app.core.config import get_settings
from app.model_gateway.base import GatewayRequest, GatewayResponse, ModelGateway, ModelGatewayError, ProviderHealth
from app.model_gateway.cost import Cost
from app.services import ai_generation


class LegacyModelGateway(ModelGateway):
    name = "legacy"

    def generate(self, request: GatewayRequest) -> GatewayResponse:
        provider = ai_generation.get_active_provider()
        model_name = self._model_name_for(provider)
        started = time.monotonic()

        dispatch = {
            "anthropic": ai_generation._generate_with_anthropic,
            "gemini": ai_generation._generate_with_gemini,
            "openrouter": ai_generation._generate_with_openrouter,
            "nvidia": ai_generation._generate_with_nvidia,
            "ollama": ai_generation._generate_with_ollama,
        }

        if provider == "mock":
            from app.services import mock_agent

            output_text = mock_agent.generate_mock_output(
                agent_key="model-gateway-legacy", node=_TransientNode(), action=_DRAFT_ACTION,
                input_artifact_count=0, retrieved_chunks=[], iteration=1, validation_feedback=None,
            )
            token_usage, cost = mock_agent.estimate_mock_usage(prompt_text=request.system_prompt, output_text=output_text)
            result = ai_generation.AgentGenerationResult(
                content_markdown=output_text, needs_clarification=False,
                prompt_tokens=token_usage["prompt_tokens"], completion_tokens=token_usage["completion_tokens"],
                total_tokens=token_usage["total_tokens"], cost=cost, used_mock=True,
            )
        else:
            try:
                result = dispatch[provider](request.system_prompt, request.user_content, request.output_token_budget)
            except ai_generation.AIGenerationError as exc:
                raise ModelGatewayError(str(exc)) from exc

        latency = time.monotonic() - started

        # See app/model_gateway/cost.py's HARD RULE — the legacy path's
        # OWN _generate_with_gemini/_generate_with_openrouter/
        # _generate_with_nvidia/_generate_with_ollama report a hardcoded
        # cost=0.0 regardless of whether the configured model is actually
        # free-tier (a known, documented gap — Phase 00 baseline section
        # 3.1); mock_agent.estimate_mock_usage reports its own arbitrary
        # non-zero placeholder rate, never a real $0. This adapter does
        # NOT correct or reinterpret either of those here: doing so would
        # mean this "legacy" adapter no longer faithfully represents
        # legacy behavior, which is exactly what it must not do — it just
        # wraps whatever `result.cost` already is (0.0 or the mock
        # placeholder) as a KNOWN Cost, unchanged. The correction lives in
        # the NEW OpenRouterGateway adapter instead
        # (app/model_gateway/openrouter_gateway.py) — opted into via
        # Settings.MODEL_GATEWAY_MODE="gateway", never silently applied to
        # the preserved legacy path.
        cost = Cost.of(result.cost)

        return GatewayResponse(
            content_markdown=result.content_markdown,
            needs_clarification=result.needs_clarification,
            clarification_questions=list(result.clarification_questions),
            requested_alias=request.alias,
            requested_provider=provider,
            requested_model=model_name,
            actual_provider=provider,
            actual_model=model_name,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cached_tokens=0,  # the legacy path never reads/records a cached-token count from any provider response
            total_tokens=result.total_tokens,
            cost=cost,
            used_mock=result.used_mock,
            latency_seconds=latency,
        )

    def check_health(self) -> ProviderHealth:
        from datetime import datetime, timezone

        provider = ai_generation.get_active_provider()
        return ProviderHealth(provider=provider, healthy=provider != "mock", checked_at=datetime.now(timezone.utc), detail=None)

    @staticmethod
    def _model_name_for(provider: str) -> str:
        settings = get_settings()
        return {
            "anthropic": settings.AI_MODEL, "gemini": settings.GEMINI_MODEL, "openrouter": settings.OPENROUTER_MODEL,
            "nvidia": settings.NVIDIA_MODEL, "ollama": settings.OLLAMA_MODEL, "mock": "mock",
        }.get(provider, provider)


# A transient, never-persisted stand-in for mock_agent.generate_mock_output's
# `node`/`action` parameters — mirrors app/services/story_lld_agent.py's
# exact same "reads only plain attributes off it" reuse pattern (see that
# module's docstring) for a caller (this gateway) that has no real
# WorkflowNode/AgentPromptRole to hand it.
class _TransientNode:
    name = "Model Gateway (legacy adapter)"
    node_key = "model-gateway-legacy"
    output_artifact_type = "gateway_response"


class _DraftAction:
    value = "draft"


_DRAFT_ACTION = _DraftAction()
