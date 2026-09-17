"""Tests for app/services/agent_jobs/failure_classification.py."""

from app.model_gateway.base import ModelGatewayError, ModelGatewayTimeoutError
from app.model_gateway.budget import BudgetExceededError, BudgetScope
from app.models.enums import JobFailureCategory
from app.services.agent_jobs.failure_classification import classify_failure


def test_timeout_is_transient():
    assert classify_failure(ModelGatewayTimeoutError("timed out")) == JobFailureCategory.TRANSIENT


def test_connection_error_is_transient():
    assert classify_failure(ConnectionError("refused")) == JobFailureCategory.TRANSIENT


def test_plain_timeout_error_is_transient():
    assert classify_failure(TimeoutError()) == JobFailureCategory.TRANSIENT


def test_budget_exceeded_is_permanent_even_though_it_could_resemble_a_retry_case():
    exc = BudgetExceededError(BudgetScope.PROJECT, "proj-1", spent_usd=10.0, max_usd=10.0)
    assert classify_failure(exc) == JobFailureCategory.PERMANENT


def test_value_error_is_permanent():
    assert classify_failure(ValueError("bad input")) == JobFailureCategory.PERMANENT


def test_generic_model_gateway_error_is_permanent_not_transient():
    """A plain ModelGatewayError (auth failure, bad request) is NOT the
    same as a timeout — must not be retried blindly."""
    assert classify_failure(ModelGatewayError("invalid api key")) == JobFailureCategory.PERMANENT


def test_unknown_exception_type_defaults_to_permanent_fail_closed():
    class _SomeWeirdError(Exception):
        pass

    assert classify_failure(_SomeWeirdError()) == JobFailureCategory.PERMANENT
