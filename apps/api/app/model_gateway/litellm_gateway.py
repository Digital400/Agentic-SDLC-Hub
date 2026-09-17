"""LiteLLMProxyGateway — calls a LiteLLM proxy's OpenAI-compatible
`/chat/completions` endpoint via plain httpx, the same "no SDK dependency
for a single well-known REST shape" convention
app/services/ai_generation.py's own OpenRouter/NVIDIA functions already
established (see that module's docstring) — no `litellm` package is
added to this codebase's dependencies.

ALIAS RESOLUTION IS DELEGATED TO THE PROXY ITSELF: a LiteLLM proxy is
configured (proxy-side, in the operator's own litellm `config.yaml`) with
named `model_list` entries — this adapter sends the alias's own string
value (e.g. "sdlc-standard") as the `model` field verbatim, and the proxy
resolves it to whatever real provider/model the operator mapped it to.
This is LiteLLM's actual, intended alias mechanism — this adapter has no
ALIAS_MODEL_MAP of its own, unlike OpenRouterGateway/LocalOllamaGateway
(which talk to a real model catalog directly and must resolve aliases
themselves).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from app.core.config import get_settings
from app.model_gateway.base import GatewayRequest, GatewayResponse, ModelGateway, ModelGatewayError, ModelGatewayTimeoutError, ProviderHealth
from app.model_gateway.cost import Cost
from app.model_gateway.health import get_shared_health_cache

_CONNECTIVITY_TIMEOUT_SECONDS = 5.0
# LiteLLM proxies commonly report a call's real cost on this response
# header when cost tracking is enabled proxy-side — see
# https://docs.litellm.ai/docs/proxy/cost_tracking (checked against
# LiteLLM's documented behavior at time of writing; a proxy without cost
# tracking enabled simply omits it, which this adapter treats as unknown,
# never as free).
_COST_HEADER = "x-litellm-response-cost"


class LiteLLMProxyGateway(ModelGateway):
    name = "litellm_proxy"

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None):
        settings = get_settings()
        self._base_url = (base_url or settings.LITELLM_PROXY_BASE_URL).rstrip("/")
        self._api_key = api_key if api_key is not None else settings.LITELLM_PROXY_API_KEY

    def generate(self, request: GatewayRequest) -> GatewayResponse:
        timeout = request.timeout_seconds or 300.0
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.monotonic()
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json={
                    "model": request.alias.value,
                    "messages": [{"role": "system", "content": request.system_prompt}, {"role": "user", "content": request.user_content}],
                    "max_tokens": request.output_token_budget,
                    **({"temperature": request.temperature} if request.temperature is not None else {}),
                    "stream": False,
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelGatewayTimeoutError(f"LiteLLM proxy generation timed out after {timeout:.1f}s: {exc}") from exc
        except httpx.HTTPError as exc:
            # Never includes the Authorization header or API key.
            raise ModelGatewayError(f"LiteLLM proxy generation failed: {exc}") from exc
        latency = time.monotonic() - started

        data = response.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        actual_model = data.get("model") or request.alias.value  # LiteLLM echoes the real resolved model in the response body

        cost_header = response.headers.get(_COST_HEADER)
        cost = Cost.of(float(cost_header)) if cost_header is not None else Cost.unknown()

        return GatewayResponse(
            content_markdown=content,
            needs_clarification=False,
            clarification_questions=[],
            requested_alias=request.alias,
            requested_provider=self.name,
            requested_model=request.alias.value,
            actual_provider=self.name,
            actual_model=actual_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            total_tokens=usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens),
            cost=cost,
            used_mock=False,
            latency_seconds=latency,
        )

    def check_health(self) -> ProviderHealth:
        try:
            response = httpx.get(f"{self._base_url}/health/liveliness", timeout=_CONNECTIVITY_TIMEOUT_SECONDS)
            healthy = response.status_code < 500
            return ProviderHealth(provider=self.name, healthy=healthy, checked_at=datetime.now(timezone.utc), detail=None if healthy else f"HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return ProviderHealth(provider=self.name, healthy=False, checked_at=datetime.now(timezone.utc), detail=str(exc))

    def cached_health(self) -> ProviderHealth:
        return get_shared_health_cache().get_or_check(self.name, self.check_health)
