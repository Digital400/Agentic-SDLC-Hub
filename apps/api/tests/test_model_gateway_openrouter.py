"""Tests for app/model_gateway/openrouter_gateway.py — the NEW,
alias-aware adapter that fixes the "cost silently reported as 0.0" gap
the legacy path carries forward unchanged."""

import httpx
import pytest

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, ModelGatewayError, ModelGatewayTimeoutError
from app.model_gateway.openrouter_gateway import OpenRouterGateway, cost_for_model


def _request(alias=ModelAlias.SDLC_SMALL) -> GatewayRequest:
    return GatewayRequest(alias=alias, system_prompt="sys", user_content="hi", output_token_budget=200)


def _mock_post(handler):
    def _post(url, *, headers, json, timeout):
        request = httpx.Request("POST", url, headers=headers, json=json)
        response = handler(request)
        response.request = request
        return response

    return _post


# --- cost_for_model -----------------------------------------------------------------------


def test_free_suffixed_model_is_always_free():
    cost = cost_for_model("meta-llama/llama-3.3-70b-instruct:free", prompt_tokens=10, completion_tokens=5, response_usage={})
    assert cost.is_known is True
    assert cost.amount_usd == 0.0


def test_paid_model_with_no_reported_cost_is_unknown():
    """The exact gap this adapter exists to close — see module docstring."""
    cost = cost_for_model("anthropic/claude-sonnet-5", prompt_tokens=10, completion_tokens=5, response_usage={})
    assert cost.is_known is False


def test_paid_model_with_a_reported_cost_field_is_known():
    cost = cost_for_model("anthropic/claude-sonnet-5", prompt_tokens=10, completion_tokens=5, response_usage={"cost": 0.0042})
    assert cost.is_known is True
    assert cost.amount_usd == 0.0042


def test_paid_model_never_silently_reports_zero():
    cost = cost_for_model("anthropic/claude-opus-5", prompt_tokens=1000, completion_tokens=500, response_usage={})
    assert cost.to_report_value() != 0.0
    assert cost.to_report_value() is None


# --- generate() ----------------------------------------------------------------------------


def test_generate_parses_content_usage_and_reports_unknown_cost_for_a_paid_model(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello."}}], "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    gateway = OpenRouterGateway(api_key="sk-or-fake")
    resp = gateway.generate(_request(ModelAlias.SDLC_STANDARD))  # maps to a non-":free" model

    assert resp.content_markdown == "Hello."
    assert resp.prompt_tokens == 12
    assert resp.completion_tokens == 6
    assert resp.cost.is_known is False


def test_generate_reports_free_cost_for_a_free_suffixed_alias(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi."}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    gateway = OpenRouterGateway(api_key="sk-or-fake")
    resp = gateway.generate(_request(ModelAlias.SDLC_SMALL))  # maps to a ":free" model

    assert resp.cost.is_known is True
    assert resp.cost.amount_usd == 0.0


def test_cached_tokens_are_parsed_when_present(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Hi."}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 40}},
        })

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    resp = OpenRouterGateway(api_key="sk-or-fake").generate(_request())
    assert resp.cached_tokens == 40


def test_no_api_key_raises_before_any_network_call():
    with pytest.raises(ModelGatewayError, match="no API key"):
        OpenRouterGateway(api_key=None).generate(_request())


def test_unmapped_alias_raises():
    with pytest.raises(ModelGatewayError, match="no model mapped"):
        OpenRouterGateway(api_key="sk-or-fake", alias_model_map={}).generate(_request())


def test_http_failure_never_leaks_the_api_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    with pytest.raises(ModelGatewayError) as exc_info:
        OpenRouterGateway(api_key="sk-or-realsecret").generate(_request())
    assert "sk-or-realsecret" not in str(exc_info.value)


def test_timeout_is_wrapped_as_gateway_timeout_error(monkeypatch):
    def _raise_timeout(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _raise_timeout)
    with pytest.raises(ModelGatewayTimeoutError):
        OpenRouterGateway(api_key="sk-or-fake").generate(_request())


def test_check_health_unhealthy_without_an_api_key():
    health = OpenRouterGateway(api_key=None).check_health()
    assert health.healthy is False
