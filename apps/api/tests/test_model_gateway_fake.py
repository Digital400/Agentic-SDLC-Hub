"""Tests for app/model_gateway/fake_gateway.py."""

import pytest

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, ModelGatewayError, ModelGatewayTimeoutError
from app.model_gateway.cost import Cost
from app.model_gateway.fake_gateway import FakeCallOutcome, FakeModelGateway


def _request(alias=ModelAlias.SDLC_STANDARD) -> GatewayRequest:
    return GatewayRequest(alias=alias, system_prompt="s", user_content="u", output_token_budget=100)


def test_scripted_outcome_is_returned():
    gw = FakeModelGateway(outcomes_by_alias={ModelAlias.SDLC_STANDARD: [FakeCallOutcome(content_markdown="hello")]})
    resp = gw.generate(_request())
    assert resp.content_markdown == "hello"
    assert resp.used_mock is True


def test_outcomes_are_consumed_front_to_back():
    gw = FakeModelGateway(outcomes_by_alias={ModelAlias.SDLC_STANDARD: [FakeCallOutcome(content_markdown="first"), FakeCallOutcome(content_markdown="second")]})
    assert gw.generate(_request()).content_markdown == "first"
    assert gw.generate(_request()).content_markdown == "second"


def test_raises_outcome_actually_raises():
    gw = FakeModelGateway(outcomes_by_alias={ModelAlias.SDLC_STANDARD: [FakeCallOutcome(raises=ModelGatewayTimeoutError("simulated"))]})
    with pytest.raises(ModelGatewayTimeoutError):
        gw.generate(_request())


def test_no_outcome_configured_raises_a_loud_error_not_a_silent_default():
    gw = FakeModelGateway()
    with pytest.raises(ModelGatewayError, match="no scripted outcome"):
        gw.generate(_request())


def test_default_outcome_used_for_any_unconfigured_alias():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="default"))
    assert gw.generate(_request(ModelAlias.SDLC_PREMIUM)).content_markdown == "default"


def test_call_log_records_every_attempted_alias_in_order():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome())
    gw.generate(_request(ModelAlias.SDLC_STANDARD))
    gw.generate(_request(ModelAlias.SDLC_SMALL))
    assert gw.call_log == [ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_SMALL]


def test_check_health_reflects_configured_healthy_flag():
    healthy_gw = FakeModelGateway(healthy=True)
    unhealthy_gw = FakeModelGateway(healthy=False)
    assert healthy_gw.check_health().healthy is True
    assert unhealthy_gw.check_health().healthy is False


def test_cost_defaults_to_free_but_is_overridable():
    gw = FakeModelGateway(outcomes_by_alias={ModelAlias.SDLC_STANDARD: [FakeCallOutcome(cost=Cost.unknown())]})
    resp = gw.generate(_request())
    assert resp.cost.is_known is False


def test_requested_and_actual_provider_model_are_populated():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(), model_name_by_alias={ModelAlias.SDLC_STANDARD: "fake-standard-v1"})
    resp = gw.generate(_request())
    assert resp.requested_provider == "fake"
    assert resp.actual_provider == "fake"
    assert resp.requested_model == "fake-standard-v1"
    assert resp.actual_model == "fake-standard-v1"
