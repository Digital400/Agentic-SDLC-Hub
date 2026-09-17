"""Timeout, retry, and fallback tests for
app/model_gateway/router.py's PolicyDrivenRouter — this phase's explicit
"Add timeout, retry and fallback tests" requirement.
"""

import pytest

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.base import GatewayRequest, ModelGatewayError, ModelGatewayTimeoutError
from app.model_gateway.budget import BudgetExceededError, BudgetScope, InMemoryBudgetTracker
from app.model_gateway.cost import Cost
from app.model_gateway.fake_gateway import FakeCallOutcome, FakeModelGateway
from app.model_gateway.policy import ModelPolicy
from app.model_gateway.router import PolicyDrivenRouter, PolicyExhaustedError


def _policy(**overrides) -> ModelPolicy:
    defaults = dict(
        policy_key="test", allowed_aliases=[ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_SMALL, ModelAlias.SDLC_PREMIUM],
        default_alias=ModelAlias.SDLC_STANDARD, fallback_aliases=[ModelAlias.SDLC_SMALL, ModelAlias.SDLC_PREMIUM],
        max_input_tokens=4000, max_output_tokens=1024, max_calls=None, max_cost_usd=None,
        timeout_seconds=30.0, temperature=None, structured_output_required=False,
        data_region_restriction=None, escalation_conditions=[],
    )
    defaults.update(overrides)
    return ModelPolicy(**defaults)


def _request() -> GatewayRequest:
    return GatewayRequest(alias=ModelAlias.SDLC_STANDARD, system_prompt="s", user_content="u", output_token_budget=100)


# --- Happy path --------------------------------------------------------------------------


def test_default_alias_succeeds_on_first_try_no_fallback_needed():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(content_markdown="ok"))
    router = PolicyDrivenRouter(gw, _policy())
    resp = router.generate(_request())
    assert resp.content_markdown == "ok"
    assert gw.call_log == [ModelAlias.SDLC_STANDARD]  # never touched a fallback


# --- Retry / fallback ----------------------------------------------------------------------


def test_falls_back_to_the_next_alias_when_the_default_fails():
    gw = FakeModelGateway(outcomes_by_alias={
        ModelAlias.SDLC_STANDARD: [FakeCallOutcome(raises=ModelGatewayError("provider down"))],
        ModelAlias.SDLC_SMALL: [FakeCallOutcome(content_markdown="fallback worked")],
    })
    router = PolicyDrivenRouter(gw, _policy())
    resp = router.generate(_request())
    assert resp.content_markdown == "fallback worked"
    assert gw.call_log == [ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_SMALL]


def test_falls_back_through_multiple_aliases_in_declared_order():
    gw = FakeModelGateway(outcomes_by_alias={
        ModelAlias.SDLC_STANDARD: [FakeCallOutcome(raises=ModelGatewayError("down"))],
        ModelAlias.SDLC_SMALL: [FakeCallOutcome(raises=ModelGatewayError("also down"))],
        ModelAlias.SDLC_PREMIUM: [FakeCallOutcome(content_markdown="last resort worked")],
    })
    router = PolicyDrivenRouter(gw, _policy())
    resp = router.generate(_request())
    assert resp.content_markdown == "last resort worked"
    assert gw.call_log == [ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_SMALL, ModelAlias.SDLC_PREMIUM]


def test_raises_policy_exhausted_when_every_alias_fails():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(raises=ModelGatewayError("always fails")))
    router = PolicyDrivenRouter(gw, _policy())
    with pytest.raises(PolicyExhaustedError) as exc_info:
        router.generate(_request())
    assert len(exc_info.value.attempts) == 3  # default + 2 fallbacks
    assert gw.call_log == [ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_SMALL, ModelAlias.SDLC_PREMIUM]


def test_policy_exhausted_error_carries_every_underlying_failure():
    gw = FakeModelGateway(outcomes_by_alias={
        ModelAlias.SDLC_STANDARD: [FakeCallOutcome(raises=ModelGatewayError("reason A"))],
        ModelAlias.SDLC_SMALL: [FakeCallOutcome(raises=ModelGatewayError("reason B"))],
        ModelAlias.SDLC_PREMIUM: [FakeCallOutcome(raises=ModelGatewayError("reason C"))],
    })
    router = PolicyDrivenRouter(gw, _policy())
    with pytest.raises(PolicyExhaustedError) as exc_info:
        router.generate(_request())
    reasons = [str(exc) for _alias, exc in exc_info.value.attempts]
    assert any("reason A" in r for r in reasons)
    assert any("reason B" in r for r in reasons)
    assert any("reason C" in r for r in reasons)


# --- Timeout ------------------------------------------------------------------------------


def test_timeout_on_default_alias_falls_back_like_any_other_failure():
    gw = FakeModelGateway(outcomes_by_alias={
        ModelAlias.SDLC_STANDARD: [FakeCallOutcome(raises=ModelGatewayTimeoutError("timed out after 30s"))],
        ModelAlias.SDLC_SMALL: [FakeCallOutcome(content_markdown="recovered")],
    })
    router = PolicyDrivenRouter(gw, _policy())
    resp = router.generate(_request())
    assert resp.content_markdown == "recovered"


def test_policy_timeout_seconds_is_applied_when_request_does_not_specify_one():
    captured_requests = []

    class _CapturingGateway(FakeModelGateway):
        def generate(self, request):
            captured_requests.append(request)
            return super().generate(request)

    gw = _CapturingGateway(default_outcome=FakeCallOutcome())
    router = PolicyDrivenRouter(gw, _policy(timeout_seconds=42.0))
    router.generate(_request())  # _request() itself sets timeout_seconds=None
    assert captured_requests[0].timeout_seconds == 42.0


def test_caller_supplied_timeout_overrides_the_policy_default():
    captured_requests = []

    class _CapturingGateway(FakeModelGateway):
        def generate(self, request):
            captured_requests.append(request)
            return super().generate(request)

    gw = _CapturingGateway(default_outcome=FakeCallOutcome())
    router = PolicyDrivenRouter(gw, _policy(timeout_seconds=42.0))
    request_with_timeout = GatewayRequest(alias=ModelAlias.SDLC_STANDARD, system_prompt="s", user_content="u", output_token_budget=100, timeout_seconds=5.0)
    router.generate(request_with_timeout)
    assert captured_requests[0].timeout_seconds == 5.0


# --- max_calls ceiling ----------------------------------------------------------------------


def test_max_calls_limits_the_whole_fallback_chain_not_per_alias():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(raises=ModelGatewayError("always fails")))
    router = PolicyDrivenRouter(gw, _policy(max_calls=2))
    with pytest.raises(PolicyExhaustedError) as exc_info:
        router.generate(_request())
    assert len(exc_info.value.attempts) == 2
    assert gw.call_log == [ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_SMALL]  # never reached SDLC_PREMIUM


def test_max_calls_of_one_never_attempts_a_fallback():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(raises=ModelGatewayError("fails")))
    router = PolicyDrivenRouter(gw, _policy(max_calls=1))
    with pytest.raises(PolicyExhaustedError):
        router.generate(_request())
    assert gw.call_log == [ModelAlias.SDLC_STANDARD]


# --- Budget integration -------------------------------------------------------------------


def test_successful_call_is_recorded_against_the_budget_tracker():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(cost=Cost.of(2.0)))
    tracker = InMemoryBudgetTracker()
    router = PolicyDrivenRouter(gw, _policy(max_cost_usd=10.0), budget_tracker=tracker, budget_scope=BudgetScope.PROJECT, budget_key="proj-1")
    router.generate(_request())
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-1") == 2.0


def test_budget_exceeded_propagates_out_of_router_generate():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(cost=Cost.of(100.0)))
    tracker = InMemoryBudgetTracker()
    router = PolicyDrivenRouter(gw, _policy(max_cost_usd=10.0), budget_tracker=tracker)
    with pytest.raises(BudgetExceededError):
        router.generate(_request())


def test_unknown_cost_never_blocks_via_budget_tracker():
    gw = FakeModelGateway(default_outcome=FakeCallOutcome(cost=Cost.unknown()))
    tracker = InMemoryBudgetTracker()
    router = PolicyDrivenRouter(gw, _policy(max_cost_usd=0.0), budget_tracker=tracker)
    resp = router.generate(_request())  # must not raise despite max_cost_usd=0.0
    assert resp.cost.is_known is False
