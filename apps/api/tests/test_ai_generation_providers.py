"""Tests for provider selection and the NVIDIA "Build" API generation path
in app/services/ai_generation.py — never makes a real network call (every
test mocks httpx.post or stubs get_settings/ollama connectivity).
"""

from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.services import ai_generation
from app.services.ai_generation import AIGenerationError, _generate_with_nvidia


def _settings(**overrides) -> SimpleNamespace:
    """A lightweight stand-in for Settings — real Settings is a pydantic
    BaseSettings that reads the environment at construction time, so tests
    override just the fields they care about rather than mutate env vars."""
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


# --- Provider priority (get_active_provider) --------------------------------------------


def test_nvidia_key_wins_over_ollama_when_both_available(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, NVIDIA_API_KEY="nvapi-fake"))
    # Ollama connectivity would normally be checked, but NVIDIA is resolved first.
    assert ai_generation.get_active_provider() == "nvidia"


def test_gemini_still_wins_over_nvidia(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY="AIzafake", NVIDIA_API_KEY="nvapi-fake"))
    assert ai_generation.get_active_provider() == "gemini"


def test_falls_through_to_ollama_when_nvidia_key_is_unset(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, NVIDIA_API_KEY=None))

    class _FakeOllamaClient:
        def __init__(self, *a, **k):
            pass

        def list(self):
            return {"models": []}

    monkeypatch.setattr(ai_generation.ollama, "Client", _FakeOllamaClient)
    assert ai_generation.get_active_provider() == "ollama"


# --- _generate_with_nvidia ---------------------------------------------------------------


def _mock_post(handler):
    def _post(url, *, headers, json, timeout):
        request = httpx.Request("POST", url, headers=headers, json=json)
        response = handler(request)
        response.request = request  # raise_for_status() needs this set
        return response

    return _post


def test_generate_with_nvidia_parses_content_and_usage(monkeypatch):
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(NVIDIA_API_KEY="nvapi-realsecret", NVIDIA_MODEL="moonshotai/kimi-k3", NVIDIA_BASE_URL="https://integrate.api.nvidia.com/v1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer nvapi-realsecret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}\n---\nHello from Kimi."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    monkeypatch.setattr(httpx, "post", _mock_post(handler))

    result = _generate_with_nvidia("system", "user", 512)

    assert "Hello from Kimi." in result.content_markdown
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.total_tokens == 15
    assert result.cost == 0.0
    assert result.used_mock is False


def test_generate_with_nvidia_raises_ai_generation_error_on_http_failure_and_never_leaks_the_key(monkeypatch):
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(NVIDIA_API_KEY="nvapi-realsecret", NVIDIA_MODEL="moonshotai/kimi-k3", NVIDIA_BASE_URL="https://integrate.api.nvidia.com/v1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))

    with pytest.raises(AIGenerationError) as exc_info:
        _generate_with_nvidia("system", "user", 512)
    assert "nvapi-realsecret" not in str(exc_info.value)
