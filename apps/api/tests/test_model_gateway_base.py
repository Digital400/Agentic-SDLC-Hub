"""Tests for app/model_gateway/aliases.py, cost.py, and base.py."""

import pytest

from app.model_gateway.aliases import ModelAlias
from app.model_gateway.cost import Cost


def test_all_six_required_aliases_exist():
    assert {a.value for a in ModelAlias} == {
        "sdlc-small", "sdlc-standard", "sdlc-premium",
        "sdlc-coding-small", "sdlc-coding-standard", "sdlc-embedding",
    }


# --- Cost -----------------------------------------------------------------------------


def test_cost_of_creates_a_known_cost():
    cost = Cost.of(1.5)
    assert cost.is_known is True
    assert cost.amount_usd == 1.5
    assert cost.to_report_value() == 1.5


def test_cost_free_is_a_real_known_zero():
    cost = Cost.free()
    assert cost.is_known is True
    assert cost.amount_usd == 0.0
    assert cost.to_report_value() == 0.0


def test_cost_unknown_carries_no_amount():
    cost = Cost.unknown()
    assert cost.is_known is False
    assert cost.amount_usd is None
    assert cost.to_report_value() is None


def test_cost_unknown_is_never_equal_to_free_despite_both_reporting_falsy_amounts():
    assert Cost.unknown() != Cost.free()
    assert Cost.unknown().to_report_value() != Cost.free().to_report_value()  # None != 0.0


def test_cost_rejects_known_with_no_amount():
    with pytest.raises(ValueError):
        Cost(is_known=True, amount_usd=None)


def test_cost_rejects_unknown_with_an_amount():
    with pytest.raises(ValueError):
        Cost(is_known=False, amount_usd=0.0)


def test_cost_rejects_negative_amount():
    with pytest.raises(ValueError):
        Cost.of(-1.0)


def test_cost_addition_known_plus_known():
    total = Cost.of(1.0) + Cost.of(2.0)
    assert total == Cost.of(3.0)


def test_cost_addition_known_plus_unknown_is_unknown():
    total = Cost.of(1.0) + Cost.unknown()
    assert total.is_known is False


def test_cost_addition_unknown_plus_unknown_is_unknown():
    total = Cost.unknown() + Cost.unknown()
    assert total.is_known is False


def test_cost_addition_never_silently_drops_to_zero():
    """The specific regression this type exists to prevent: summing a
    known cost with an unknown one must never quietly produce 0.0."""
    total = Cost.free() + Cost.unknown()
    assert total.to_report_value() is None
    assert total.to_report_value() != 0.0
