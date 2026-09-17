"""OpenRouterGateway — a NEW, alias-aware ModelGateway adapter, added
"where required for transition" per this phase's instructions: it exists
specifically to fix the cost-representation gap the legacy path carries
forward unchanged (see legacy_gateway.py's own comment on this exact
point) — a resolved model that isn't confirmed free-tier reports
Cost.unknown(), never Cost.of(0.0).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from app.core.config import get_settings
from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, GatewayResponse, ModelGateway, ModelGatewayError, ModelGatewayTimeoutError, ProviderHealth
from app.model_gateway.cost import Cost
from app.model_gateway.health import get_shared_health_cache

_CONNECTIVITY_TIMEOUT_SECONDS = 5.0

# Every alias maps to a real OpenRouter model slug. Only the two aliases
# below default to a confirmed ":free"-suffixed model — every other alias
# defaults to a real, non-free model, so its cost is reported as UNKNOWN
# (this adapter has no per-model pricing table; see cost_for_model below)
# rather than fabricated as $0. A deployment wanting a different mapping
# overrides this via the constructor.
ALIAS_MODEL_MAP: dict[ModelAlias, str] = {
    ModelAlias.SDLC_SMALL: "meta-llama/llama-3.3-70b-instruct:free",
    ModelAlias.SDLC_STANDARD: "anthropic/claude-sonnet-5",
    ModelAlias.SDLC_PREMIUM: "anthropic/claude-opus-5",
    ModelAlias.SDLC_CODING_SMALL: "qwen/qwen-2.5-coder-32b-instruct:free",
    ModelAlias.SDLC_CODING_STANDARD: "anthropic/claude-sonnet-5",
}


def cost_for_model(model: str, *, prompt_tokens: int, completion_tokens: int, response_usage: dict) -> Cost:
    """A ":free"-suffixed model slug is OpenRouter's own explicit
    contract that it costs nothing — trusted as Cost.free(). A response
    that itself reports a real `usage.cost`/`total_cost` field (some
    OpenRouter responses do, when the caller's account has cost tracking
    enabled) is trusted as that exact figure. Anything else is
    Cost.unknown() — this adapter does not maintain, and will not guess
    from, a per-model price list (see app/services/ai_generation.py's own
    _MODEL_PRICING_PER_MTOK for why a hardcoded table is fragile: it only
    covers three Anthropic model names today and silently reports 0.0 for
    everything else — the exact failure mode this function exists to
    avoid repeating)."""
    if model.endswith(":free"):
        return Cost.free()
    reported = response_usage.get("cost") or response_usage.get("total_cost")
    if isinstance(reported, (int, float)):
        return Cost.of(float(reported))
    return Cost.unknown()


class OpenRouterGateway(ModelGateway):
    name = "openrouter"

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None, alias_model_map: dict[ModelAlias, str] | None = None):
        settings = get_settings()
        self._base_url = base_url or settings.OPENROUTER_BASE_URL
        self._api_key = api_key or settings.OPENROUTER_API_KEY
        self._alias_model_map = alias_model_map if alias_model_map is not None else ALIAS_MODEL_MAP

    def generate(self, request: GatewayRequest) -> GatewayResponse:
        if not self._api_key:
            raise ModelGatewayError("OpenRouterGateway has no API key configured.")
        if request.alias not in self._alias_model_map:
            raise ModelGatewayError(f"OpenRouterGateway has no model mapped for alias '{request.alias.value}'.")
        model = self._alias_model_map[request.alias]
        timeout = request.timeout_seconds or 300.0

        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "system", "content": request.system_prompt}, {"role": "user", "content": request.user_content}],
                    "max_tokens": request.output_token_budget,
                    **({"temperature": request.temperature} if request.temperature is not None else {}),
                    "stream": False,
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelGatewayTimeoutError(f"OpenRouter generation timed out after {timeout:.1f}s: {exc}") from exc
        except httpx.HTTPError as exc:
            # Never includes the Authorization header or API key — same
            # discipline as ai_generation.py's _generate_with_openrouter.
            raise ModelGatewayError(f"OpenRouter generation failed: {exc}") from exc
        latency = time.monotonic() - started

        data = response.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0

        return GatewayResponse(
            content_markdown=content,
            needs_clarification=False,
            clarification_questions=[],
            requested_alias=request.alias,
            requested_provider=self.name,
            requested_model=model,
            actual_provider=self.name,
            actual_model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            total_tokens=usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens),
            cost=cost_for_model(model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, response_usage=usage),
            used_mock=False,
            latency_seconds=latency,
        )

    def check_health(self) -> ProviderHealth:
        if not self._api_key:
            return ProviderHealth(provider=self.name, healthy=False, checked_at=datetime.now(timezone.utc), detail="no API key configured")
        try:
            response = httpx.get(f"{self._base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {self._api_key}"}, timeout=_CONNECTIVITY_TIMEOUT_SECONDS)
            healthy = response.status_code < 500
            return ProviderHealth(provider=self.name, healthy=healthy, checked_at=datetime.now(timezone.utc), detail=None if healthy else f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return ProviderHealth(provider=self.name, healthy=False, checked_at=datetime.now(timezone.utc), detail=str(exc))

    def cached_health(self) -> ProviderHealth:
        return get_shared_health_cache().get_or_check(self.name, self.check_health)
