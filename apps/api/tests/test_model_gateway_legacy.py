"""Tests for app/model_gateway/legacy_gateway.py — LegacyModelGateway
delegates to app/services/ai_generation.py's existing, unmodified
provider logic; this file verifies the delegation itself, not
ai_generation.py's own provider behavior (already covered by
tests/test_ai_generation_providers.py).
"""

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, ModelGatewayError
from app.model_gateway.legacy_gateway import LegacyModelGateway
from app.services import ai_generation


def _settings(**overrides) -> SimpleNamespace:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


def _request() -> GatewayRequest:
    return GatewayRequest(alias=ModelAlias.SDLC_STANDARD, system_prompt="sys", user_content="hello", output_token_budget=200)


def test_mock_path_delegates_through_and_returns_a_valid_response(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY=None))

    class _UnreachableOllama:
        def __init__(self, *a, **k):
            pass

        def list(self):
            raise ConnectionError("no ollama running")

    monkeypatch.setattr(ai_generation.ollama, "Client", _UnreachableOllama)

    gateway = LegacyModelGateway()
    resp = gateway.generate(_request())

    assert resp.requested_provider == "mock"
    assert resp.actual_provider == "mock"
    assert resp.used_mock is True
    assert resp.cost.is_known is True  # mock always reports a known (if arbitrary placeholder) cost
    assert resp.content_markdown


def test_requested_and_actual_provider_are_identical_on_the_legacy_adapter(monkeypatch):
    """The legacy path has no separate requested-vs-served distinction —
    see base.py's GatewayResponse docstring for where that distinction
    actually matters (the new gateways + router.py's fallback chain)."""
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY=None))

    class _FakeOllamaClient:
        def __init__(self, *a, **k):
            pass

        def list(self):
            return {"models": []}

        def chat(self, **kwargs):
            return {"message": {"content": "{}\n---\nHello from Ollama."}, "prompt_eval_count": 10, "eval_count": 5}

    monkeypatch.setattr(ai_generation.ollama, "Client", _FakeOllamaClient)

    resp = LegacyModelGateway().generate(_request())
    assert resp.requested_provider == resp.actual_provider
    assert resp.requested_model == resp.actual_model
    assert resp.requested_provider == "ollama"


def test_generation_failure_is_wrapped_as_model_gateway_error(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-ant-fake", GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY=None))

    def _raise(*a, **k):
        raise ai_generation.AIGenerationError("upstream auth failure")

    monkeypatch.setattr(ai_generation, "_generate_with_anthropic", _raise)

    with pytest.raises(ModelGatewayError, match="upstream auth failure"):
        LegacyModelGateway().generate(_request())


def test_check_health_reports_mock_as_unhealthy(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY=None))
    monkeypatch.setattr(ai_generation.ollama, "Client", lambda *a, **k: SimpleNamespace(list=lambda: (_ for _ in ()).throw(ConnectionError())))

    health = LegacyModelGateway().check_health()
    assert health.provider == "mock"
    assert health.healthy is False


def test_check_health_reports_a_real_provider_as_healthy(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY="sk-ant-fake", GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY=None))
    health = LegacyModelGateway().check_health()
    assert health.provider == "anthropic"
    assert health.healthy is True
