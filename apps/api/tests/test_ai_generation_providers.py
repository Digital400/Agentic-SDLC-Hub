"""Tests for provider selection and the OpenRouter/NVIDIA "Build" API
generation paths in app/services/ai_generation.py — never makes a real
network call (every test mocks httpx.post/get or stubs get_settings/ollama
connectivity).
"""

from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.services import ai_generation
from app.services.ai_generation import AIGenerationError, _generate_with_nvidia, _generate_with_openrouter


def _settings(**overrides) -> SimpleNamespace:
    """A lightweight stand-in for Settings — real Settings is a pydantic
    BaseSettings that reads the environment at construction time, so tests
    override just the fields they care about rather than mutate env vars."""
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


# --- Provider priority (get_active_provider) --------------------------------------------


def _fake_ollama_client(monkeypatch):
    class _FakeOllamaClient:
        def __init__(self, *a, **k):
            pass

        def list(self):
            return {"models": []}

    monkeypatch.setattr(ai_generation.ollama, "Client", _FakeOllamaClient)


def test_openrouter_key_wins_over_nvidia_and_ollama_when_reachable(monkeypatch):
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY="sk-or-fake", NVIDIA_API_KEY="nvapi-fake"),
    )
    monkeypatch.setattr(ai_generation, "_openai_compatible_endpoint_is_reachable", lambda base_url, api_key: True)
    assert ai_generation.get_active_provider() == "openrouter"


def test_nvidia_key_wins_over_ollama_when_available_and_reachable_and_openrouter_unset(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY="nvapi-fake"))
    monkeypatch.setattr(ai_generation, "_openai_compatible_endpoint_is_reachable", lambda base_url, api_key: True)
    # Ollama connectivity would normally be checked, but NVIDIA is resolved first.
    assert ai_generation.get_active_provider() == "nvidia"


def test_gemini_still_wins_over_openrouter_and_nvidia(monkeypatch):
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY="AIzafake", OPENROUTER_API_KEY="sk-or-fake", NVIDIA_API_KEY="nvapi-fake"),
    )
    assert ai_generation.get_active_provider() == "gemini"


def test_falls_through_to_ollama_when_nothing_hosted_is_set(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY=None))
    _fake_ollama_client(monkeypatch)
    assert ai_generation.get_active_provider() == "ollama"


def test_falls_through_openrouter_to_nvidia_when_openrouter_is_unreachable(monkeypatch):
    """A stuck/hanging OpenRouter endpoint must not block NVIDIA from
    still being tried."""
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY="sk-or-fake", NVIDIA_API_KEY="nvapi-fake"),
    )

    def _reachable(base_url, api_key):
        return "nvidia" in base_url

    monkeypatch.setattr(ai_generation, "_openai_compatible_endpoint_is_reachable", _reachable)
    assert ai_generation.get_active_provider() == "nvidia"


def test_falls_through_to_ollama_when_nvidia_is_unreachable(monkeypatch):
    """Regression test: a stuck/hanging NVIDIA endpoint must not make every
    single agent-run request commit to the full multi-minute generation
    timeout — get_active_provider should fail over to Ollama (or mock)
    within the fast connectivity check's own bound instead."""
    monkeypatch.setattr(ai_generation, "get_settings", lambda: _settings(ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None, OPENROUTER_API_KEY=None, NVIDIA_API_KEY="nvapi-fake"))
    monkeypatch.setattr(ai_generation, "_openai_compatible_endpoint_is_reachable", lambda base_url, api_key: False)
    _fake_ollama_client(monkeypatch)
    assert ai_generation.get_active_provider() == "ollama"


def test_reachability_check_never_blocks_on_a_hung_connection(monkeypatch):
    """_openai_compatible_endpoint_is_reachable itself must return False
    (not hang or raise) on a genuine network timeout — the exact failure
    mode observed in practice against the real NVIDIA endpoint."""
    def _raise_timeout(*a, **k):
        raise httpx.ConnectTimeout("connection timed out")

    monkeypatch.setattr(ai_generation.httpx, "get", _raise_timeout)
    assert ai_generation._openai_compatible_endpoint_is_reachable("https://integrate.api.nvidia.com/v1", "nvapi-fake") is False


def test_reachability_check_treats_an_error_response_as_reachable(monkeypatch):
    """A real HTTP response (even 401/404) means the network path works —
    only a connection that never responds at all should be treated as
    unreachable; an actual bad-key error should surface fast from the real
    generate call, not be silently masked by falling through to the next
    provider."""
    def _fake_get(url, *, headers, timeout):
        request = httpx.Request("GET", url)
        return httpx.Response(401, json={"detail": "Unauthorized"}, request=request)

    monkeypatch.setattr(ai_generation.httpx, "get", _fake_get)
    assert ai_generation._openai_compatible_endpoint_is_reachable("https://integrate.api.nvidia.com/v1", "nvapi-fake") is True


# --- _generate_with_openrouter -----------------------------------------------------------


def _mock_post(handler):
    def _post(url, *, headers, json, timeout):
        request = httpx.Request("POST", url, headers=headers, json=json)
        response = handler(request)
        response.request = request  # raise_for_status() needs this set
        return response

    return _post


def test_generate_with_openrouter_parses_content_and_usage(monkeypatch):
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(OPENROUTER_API_KEY="sk-or-realsecret", OPENROUTER_MODEL="meta-llama/llama-3.3-70b-instruct:free", OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-or-realsecret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}\n---\nHello from OpenRouter."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        )

    monkeypatch.setattr(httpx, "post", _mock_post(handler))

    result = _generate_with_openrouter("system", "user", 512)

    assert "Hello from OpenRouter." in result.content_markdown
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 6
    assert result.total_tokens == 18
    assert result.cost == 0.0
    assert result.used_mock is False


def test_generate_with_openrouter_raises_ai_generation_error_on_http_failure_and_never_leaks_the_key(monkeypatch):
    monkeypatch.setattr(
        ai_generation, "get_settings",
        lambda: _settings(OPENROUTER_API_KEY="sk-or-realsecret", OPENROUTER_MODEL="meta-llama/llama-3.3-70b-instruct:free", OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))

    with pytest.raises(AIGenerationError) as exc_info:
        _generate_with_openrouter("system", "user", 512)
    assert "sk-or-realsecret" not in str(exc_info.value)


# --- _generate_with_nvidia ---------------------------------------------------------------


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
