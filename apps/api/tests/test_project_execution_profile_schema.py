"""Tests for the Pydantic contracts in
app/schemas/project_execution_profile.py — most importantly the
NAMES-ONLY enforcement on environment_variable_names (see that module's
HARD RULE)."""

import pytest
from pydantic import ValidationError

from app.models.enums import NetworkPolicyDefault
from app.schemas.project_execution_profile import (
    NetworkPolicy,
    ProjectExecutionProfileBase,
    QualityGate,
)


def _base(**overrides):
    defaults = dict()
    defaults.update(overrides)
    return ProjectExecutionProfileBase(**defaults)


def test_environment_variable_names_accepts_valid_identifier_shaped_names():
    profile = _base(environment_variable_names=["DATABASE_URL", "API_BASE_URL", "_PRIVATE", "V2"])
    assert profile.environment_variable_names == ["DATABASE_URL", "API_BASE_URL", "_PRIVATE", "V2"]


def test_environment_variable_names_rejects_a_name_equals_value_pair():
    with pytest.raises(ValidationError):
        _base(environment_variable_names=["DATABASE_URL=postgresql://user:pass@host/db"])


def test_environment_variable_names_rejects_a_bare_value_looking_string():
    with pytest.raises(ValidationError):
        _base(environment_variable_names=["sk-abcdef123456"])  # contains a '-', not an identifier


def test_environment_variable_names_rejects_a_name_starting_with_a_digit():
    with pytest.raises(ValidationError):
        _base(environment_variable_names=["2FA_SECRET"])


def test_environment_variable_names_rejects_whitespace():
    with pytest.raises(ValidationError):
        _base(environment_variable_names=["MY VAR"])


def test_network_policy_defaults_to_deny():
    policy = NetworkPolicy()
    assert policy.default == NetworkPolicyDefault.DENY
    assert policy.allowed_domains == []


def test_network_policy_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        NetworkPolicy.model_validate({"default": "DENY", "unexpected_field": True})


def test_quality_gate_severity_is_constrained_to_two_values():
    QualityGate(name="x", description="y", severity="BLOCKING")
    QualityGate(name="x", description="y", severity="ADVISORY")
    with pytest.raises(ValidationError):
        QualityGate(name="x", description="y", severity="CRITICAL")


def test_profile_base_forbids_undeclared_fields():
    with pytest.raises(ValidationError):
        ProjectExecutionProfileBase.model_validate({"anthropic_model": "claude-opus-5"})
