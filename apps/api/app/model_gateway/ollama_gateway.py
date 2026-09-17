"""LocalOllamaGateway — a NEW, alias-aware ModelGateway adapter for local
Ollama inference. Distinct from app/services/ai_generation.py's own
_generate_with_ollama (which app/model_gateway/legacy_gateway.py still
calls, unmodified, on the preserved legacy path) — this adapter resolves
a ModelAlias to a locally-pulled Ollama model name, uses cached health
(see app/model_gateway/health.py) instead of a live client.list() call on
every request, and always reports Cost.free() (Ollama is genuinely free —
local inference, no metered API — never "unknown").
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
import ollama

from app.core.config import get_settings
from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, GatewayResponse, ModelGateway, ModelGatewayError, ModelGatewayTimeoutError, ProviderHealth
from app.model_gateway.cost import Cost
from app.model_gateway.health import get_shared_health_cache

_CONNECTIVITY_TIMEOUT_SECONDS = 5.0
_DEFAULT_GENERATION_TIMEOUT_SECONDS = 180.0

# Every alias maps to a real, small-footprint, commonly-pulled Ollama
# model — a deployment with different locally-pulled models overrides
# this via Settings.OLLAMA_MODEL for the legacy path; this adapter's own
# mapping is deliberately independent (aliases are a NEW concept the
# legacy path never had a use for).
ALIAS_MODEL_MAP: dict[ModelAlias, str] = {
    ModelAlias.SDLC_SMALL: "llama3.2:3b",
    ModelAlias.SDLC_STANDARD: "llama3.1:8b",
    ModelAlias.SDLC_PREMIUM: "llama3.1:8b",  # Ollama's local catalog has no larger tier pulled by default — see class docstring
    ModelAlias.SDLC_CODING_SMALL: "qwen2.5-coder:7b",
    ModelAlias.SDLC_CODING_STANDARD: "qwen2.5-coder:7b",
}


class LocalOllamaGateway(ModelGateway):
    """`ALIAS_MODEL_MAP` has no SDLC_EMBEDDING entry — Ollama CAN serve
    embedding models, but this adapter doesn't claim to support one until
    a specific model is chosen and pulled; requesting SDLC_EMBEDDING
    raises ModelGatewayError rather than silently falling back to a
    chat model that was never meant to produce embeddings."""

    name = "ollama"

    def __init__(self, *, base_url: str | None = None):
        settings = get_settings()
        self._base_url = base_url or settings.OLLAMA_BASE_URL

    def generate(self, request: GatewayRequest) -> GatewayResponse:
        if request.alias not in ALIAS_MODEL_MAP:
            raise ModelGatewayError(f"LocalOllamaGateway has no model mapped for alias '{request.alias.value}'.")
        model_name = ALIAS_MODEL_MAP[request.alias]

        timeout = request.timeout_seconds or _DEFAULT_GENERATION_TIMEOUT_SECONDS
        client = ollama.Client(host=self._base_url, timeout=timeout)
        started = time.monotonic()
        try:
            response = client.chat(
                model=model_name,
                messages=[{"role": "system", "content": request.system_prompt}, {"role": "user", "content": request.user_content}],
                options={
                    "num_predict": request.output_token_budget,
                    "temperature": request.temperature if request.temperature is not None else 0.7,
                },
            )
        except httpx.TimeoutException as exc:
            # ollama's Python client wraps httpx — a real timeout surfaces
            # as exactly this exception type (not merely "took a while"),
            # so this is a precise catch, not a latency-based guess.
            raise ModelGatewayTimeoutError(f"Ollama generation timed out after {timeout:.1f}s: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — mirrors ai_generation._generate_with_ollama's own catch-all for every other failure mode
            raise ModelGatewayError(f"Ollama generation failed: {exc}") from exc
        latency = time.monotonic() - started

        content = response.get("message", {}).get("content", "")
        prompt_tokens = response.get("prompt_eval_count", 0)
        completion_tokens = response.get("eval_count", 0)

        return GatewayResponse(
            content_markdown=content,
            needs_clarification=False,  # this adapter doesn't parse ai_generation.py's two-part clarification contract — a caller opting into the gateway path is responsible for its own response-parsing convention
            clarification_questions=[],
            requested_alias=request.alias,
            requested_provider=self.name,
            requested_model=model_name,
            actual_provider=self.name,
            actual_model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=0,  # Ollama's /api/chat response reports no separate cached-token count
            total_tokens=prompt_tokens + completion_tokens,
            cost=Cost.free(),
            used_mock=False,
            latency_seconds=latency,
        )

    def check_health(self) -> ProviderHealth:
        try:
            ollama.Client(host=self._base_url, timeout=_CONNECTIVITY_TIMEOUT_SECONDS).list()
            return ProviderHealth(provider=self.name, healthy=True, checked_at=datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 — any failure means "not reachable," not a crash
            return ProviderHealth(provider=self.name, healthy=False, checked_at=datetime.now(timezone.utc), detail=str(exc))

    def cached_health(self) -> ProviderHealth:
        """The hot-path-safe way to check health — see
        app/model_gateway/health.py's module docstring for why this
        exists instead of calling check_health() directly on every
        request."""
        return get_shared_health_cache().get_or_check(self.name, self.check_health)
