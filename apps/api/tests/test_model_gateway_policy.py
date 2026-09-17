"""Tests for app/model_gateway/policy.py."""

import pytest
from pydantic import ValidationError

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.policy import DEFAULT_MODEL_POLICY, EscalationCondition, ModelPolicy


def _policy(**overrides):
    defaults = dict(
        policy_key="test", allowed_aliases=[ModelAlias.SDLC_SMALL, ModelAlias.SDLC_STANDARD],
        default_alias=ModelAlias.SDLC_STANDARD, fallback_aliases=[ModelAlias.SDLC_SMALL],
        max_input_tokens=4000, max_output_tokens=1024, max_calls=2, max_cost_usd=1.0,
        timeout_seconds=30.0, temperature=0.5, structured_output_required=False,
        data_region_restriction=None, escalation_conditions=[],
    )
    defaults.update(overrides)
    return ModelPolicy(**defaults)


def test_default_model_policy_is_valid():
    assert DEFAULT_MODEL_POLICY.default_alias in DEFAULT_MODEL_POLICY.allowed_aliases
    assert set(DEFAULT_MODEL_POLICY.allowed_aliases) == set(ModelAlias)


def test_valid_policy_constructs():
    policy = _policy()
    assert policy.default_alias == ModelAlias.SDLC_STANDARD


def test_default_alias_must_be_in_allowed_aliases():
    with pytest.raises(ValidationError, match="default_alias"):
        _policy(default_alias=ModelAlias.SDLC_PREMIUM)


def test_fallback_aliases_must_be_subset_of_allowed_aliases():
    with pytest.raises(ValidationError, match="fallback_aliases"):
        _policy(fallback_aliases=[ModelAlias.SDLC_PREMIUM])


def test_escalation_condition_target_must_be_allowed():
    with pytest.raises(ValidationError, match="escalation_conditions"):
        _policy(escalation_conditions=[EscalationCondition(trigger="low_quality_score", escalate_to_alias=ModelAlias.SDLC_PREMIUM)])


def test_escalation_condition_valid_when_target_is_allowed():
    policy = _policy(
        allowed_aliases=[ModelAlias.SDLC_SMALL, ModelAlias.SDLC_STANDARD, ModelAlias.SDLC_PREMIUM],
        escalation_conditions=[EscalationCondition(trigger="low_quality_score", escalate_to_alias=ModelAlias.SDLC_PREMIUM)],
    )
    assert policy.escalation_conditions[0].trigger == "low_quality_score"


def test_policy_forbids_undeclared_fields():
    with pytest.raises(ValidationError):
        ModelPolicy.model_validate({"policy_key": "x", "vendor_specific_field": "should not exist"})


def test_max_calls_must_be_positive():
    with pytest.raises(ValidationError):
        _policy(max_calls=0)


def test_max_cost_cannot_be_negative():
    with pytest.raises(ValidationError):
        _policy(max_cost_usd=-1.0)


def test_temperature_bounds():
    with pytest.raises(ValidationError):
        _policy(temperature=3.0)


def test_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        _policy(timeout_seconds=0)


def test_policy_fields_all_required_by_this_phase_are_present():
    """Cross-check against the literal field list this phase's
    instructions require."""
    fields = set(ModelPolicy.model_fields.keys())
    required = {
        "allowed_aliases", "default_alias", "fallback_aliases", "max_input_tokens", "max_output_tokens",
        "max_calls", "max_cost_usd", "timeout_seconds", "temperature", "structured_output_required",
        "data_region_restriction", "escalation_conditions",
    }
    assert required <= fields
