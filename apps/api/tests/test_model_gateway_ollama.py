"""Tests for app/model_gateway/ollama_gateway.py — the NEW, alias-aware
Local/Ollama adapter (distinct from ai_generation.py's own
_generate_with_ollama, still used unmodified by LegacyModelGateway)."""

from types import SimpleNamespace

import httpx
import pytest

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, ModelGatewayError, ModelGatewayTimeoutError
from app.model_gateway.ollama_gateway import LocalOllamaGateway


def _request(alias=ModelAlias.SDLC_SMALL) -> GatewayRequest:
    return GatewayRequest(alias=alias, system_prompt="sys", user_content="hi", output_token_budget=100)


def test_generate_parses_content_and_token_counts(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def chat(self, **kwargs):
            return {"message": {"content": "Hello from local Ollama."}, "prompt_eval_count": 20, "eval_count": 8}

    monkeypatch.setattr("app.model_gateway.ollama_gateway.ollama.Client", _FakeClient)

    resp = LocalOllamaGateway().generate(_request())
    assert resp.content_markdown == "Hello from local Ollama."
    assert resp.prompt_tokens == 20
    assert resp.completion_tokens == 8
    assert resp.total_tokens == 28
    assert resp.cached_tokens == 0
    assert resp.cost.is_known is True
    assert resp.cost.amount_usd == 0.0  # always free — see class docstring
    assert resp.requested_provider == "ollama"
    assert resp.actual_provider == "ollama"


def test_unmapped_alias_raises_immediately():
    with pytest.raises(ModelGatewayError, match="no model mapped"):
        LocalOllamaGateway().generate(_request(ModelAlias.SDLC_EMBEDDING))


def test_real_timeout_exception_is_wrapped_as_gateway_timeout(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def chat(self, **kwargs):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("app.model_gateway.ollama_gateway.ollama.Client", _FakeClient)

    with pytest.raises(ModelGatewayTimeoutError):
        LocalOllamaGateway().generate(_request())


def test_other_failures_are_wrapped_as_a_plain_gateway_error(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def chat(self, **kwargs):
            raise ConnectionError("connection refused")

    monkeypatch.setattr("app.model_gateway.ollama_gateway.ollama.Client", _FakeClient)

    with pytest.raises(ModelGatewayError):
        LocalOllamaGateway().generate(_request())


def test_check_health_reports_healthy_when_list_succeeds(monkeypatch):
    monkeypatch.setattr("app.model_gateway.ollama_gateway.ollama.Client", lambda *a, **k: SimpleNamespace(list=lambda: {"models": []}))
    health = LocalOllamaGateway().check_health()
    assert health.healthy is True
    assert health.provider == "ollama"


def test_check_health_reports_unhealthy_on_failure(monkeypatch):
    def _raise_client(*a, **k):
        raise ConnectionError("no server")

    monkeypatch.setattr("app.model_gateway.ollama_gateway.ollama.Client", _raise_client)
    health = LocalOllamaGateway().check_health()
    assert health.healthy is False
    assert health.detail is not None


def test_cached_health_uses_the_shared_cache(monkeypatch):
    from app.model_gateway import health as health_module

    health_module.get_shared_health_cache().invalidate()
    calls = []

    def _client(*a, **k):
        calls.append(1)
        return SimpleNamespace(list=lambda: {"models": []})

    monkeypatch.setattr("app.model_gateway.ollama_gateway.ollama.Client", _client)

    gateway = LocalOllamaGateway()
    gateway.cached_health()
    gateway.cached_health()
    assert len(calls) == 1  # second call served from cache, not a new probe
    health_module.get_shared_health_cache().invalidate()
