"""Tests for app/model_gateway/budget.py — company/project budget
support."""

import pytest

from app.model_gateway.budget import BudgetExceededError, BudgetScope, InMemoryBudgetTracker
from app.model_gateway.cost import Cost


def test_records_known_spend_and_reports_it_back():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(1.5), max_cost_usd=None)
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-1") == 1.5


def test_raises_when_a_call_would_exceed_the_max():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(4.0), max_cost_usd=5.0)
    with pytest.raises(BudgetExceededError):
        tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(2.0), max_cost_usd=5.0)


def test_does_not_raise_when_exactly_at_the_max():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(5.0), max_cost_usd=5.0)
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-1") == 5.0


def test_none_max_cost_never_raises():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(1_000_000.0), max_cost_usd=None)


def test_unknown_cost_never_raises_and_is_tracked_separately():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.unknown(), max_cost_usd=0.01)
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-1") == 0.0
    assert tracker.unknown_call_count(scope=BudgetScope.PROJECT, key="proj-1") == 1


def test_company_and_project_scopes_are_independent():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.COMPANY, key="acme", cost=Cost.of(10.0), max_cost_usd=None)
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="acme", cost=Cost.of(1.0), max_cost_usd=None)
    assert tracker.spent_usd(scope=BudgetScope.COMPANY, key="acme") == 10.0
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="acme") == 1.0


def test_different_keys_within_the_same_scope_are_independent():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(1.0), max_cost_usd=None)
    tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-2", cost=Cost.of(2.0), max_cost_usd=None)
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-1") == 1.0
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-2") == 2.0


def test_spend_accumulates_across_multiple_calls():
    tracker = InMemoryBudgetTracker()
    for _ in range(3):
        tracker.check_and_reserve(scope=BudgetScope.PROJECT, key="proj-1", cost=Cost.of(1.0), max_cost_usd=None)
    assert tracker.spent_usd(scope=BudgetScope.PROJECT, key="proj-1") == 3.0


def test_exceeded_error_carries_scope_and_amounts():
    tracker = InMemoryBudgetTracker()
    tracker.check_and_reserve(scope=BudgetScope.COMPANY, key="acme", cost=Cost.of(9.0), max_cost_usd=10.0)
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.check_and_reserve(scope=BudgetScope.COMPANY, key="acme", cost=Cost.of(5.0), max_cost_usd=10.0)
    assert exc_info.value.scope == BudgetScope.COMPANY
    assert exc_info.value.key == "acme"
    assert exc_info.value.spent_usd == 9.0
    assert exc_info.value.max_usd == 10.0
