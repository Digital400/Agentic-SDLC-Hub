"""Tests for app/model_gateway/litellm_gateway.py."""

import httpx
import pytest

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, ModelGatewayError, ModelGatewayTimeoutError
from app.model_gateway.litellm_gateway import LiteLLMProxyGateway


def _request(alias=ModelAlias.SDLC_STANDARD) -> GatewayRequest:
    return GatewayRequest(alias=alias, system_prompt="sys", user_content="hi", output_token_budget=200)


def _mock_post(handler):
    def _post(url, *, headers, json, timeout):
        request = httpx.Request("POST", url, headers=headers, json=json)
        response = handler(request)
        response.request = request
        return response

    return _post


def test_sends_the_alias_itself_as_the_model_field(monkeypatch):
    """The whole point of LiteLLM's alias mechanism — see module
    docstring: this adapter never resolves the alias itself."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_module

        captured["body"] = json_module.loads(request.content)
        return httpx.Response(200, json={"model": "gpt-4o-mini", "choices": [{"message": {"content": "Hi."}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-fake").generate(_request(ModelAlias.SDLC_STANDARD))
    assert captured["body"]["model"] == "sdlc-standard"


def test_actual_model_is_read_from_the_response_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "anthropic/claude-sonnet-5", "choices": [{"message": {"content": "Hi."}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    resp = LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-fake").generate(_request())

    assert resp.requested_model == "sdlc-standard"
    assert resp.actual_model == "anthropic/claude-sonnet-5"  # differs from requested — the proxy's own resolution


def test_cost_header_present_is_parsed_as_known(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-litellm-response-cost": "0.0031"}, json={"choices": [{"message": {"content": "Hi."}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    resp = LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-fake").generate(_request())
    assert resp.cost.is_known is True
    assert resp.cost.amount_usd == 0.0031


def test_cost_header_absent_is_unknown_not_zero(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi."}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    resp = LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-fake").generate(_request())
    assert resp.cost.is_known is False
    assert resp.cost.to_report_value() != 0.0


def test_no_api_key_omits_authorization_header_rather_than_failing(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi."}}], "usage": {}})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    LiteLLMProxyGateway(base_url="http://localhost:4000", api_key=None).generate(_request())
    assert "authorization" not in captured["headers"]


def test_http_failure_never_leaks_the_api_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    monkeypatch.setattr(httpx, "post", _mock_post(handler))
    with pytest.raises(ModelGatewayError) as exc_info:
        LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-realsecret").generate(_request())
    assert "sk-realsecret" not in str(exc_info.value)


def test_timeout_is_wrapped(monkeypatch):
    def _raise_timeout(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _raise_timeout)
    with pytest.raises(ModelGatewayTimeoutError):
        LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-fake").generate(_request())


def test_check_health_uses_the_liveliness_endpoint(monkeypatch):
    captured = {}

    def _get(url, timeout):
        captured["url"] = url
        return httpx.Response(200, json={"status": "healthy"})

    monkeypatch.setattr(httpx, "get", _get)
    health = LiteLLMProxyGateway(base_url="http://localhost:4000", api_key="sk-fake").check_health()
    assert health.healthy is True
    assert captured["url"].endswith("/health/liveliness")
