"""The "a story's PR must be reviewable by one human" rule — see
app/services/review_time.py."""

import pytest

from app.services.review_time import (
    MAX_WORST_CASE_MINUTES,
    parse_review_time,
    review_time_problems,
)


@pytest.mark.parametrize(
    "raw,best,typical,worst",
    [
        ("5 / 15 / 30", 5, 15, 30),
        ("5/15/30", 5, 15, 30),
        ("5, 15, 30", 5, 15, 30),
        ("5 - 15 - 30", 5, 15, 30),
        ("10 / 20 / 30 (pair recommended for the migration step)", 10, 20, 30),
    ],
)
def test_parses_three_numbers_in_various_separator_styles(raw, best, typical, worst):
    estimate = parse_review_time(raw)
    assert (estimate.best_case_minutes, estimate.typical_minutes, estimate.worst_case_minutes) == (best, typical, worst)


def test_pair_recommended_is_detected():
    assert parse_review_time("10 / 20 / 30 (pair recommended for the migration step)").pair_recommended is True
    assert parse_review_time("5 / 15 / 30").pair_recommended is False


def test_a_single_number_is_treated_as_the_worst_case():
    estimate = parse_review_time("20 minutes")
    assert estimate.worst_case_minutes == 20 and estimate.best_case_minutes is None


def test_empty_and_unparseable_values():
    empty = parse_review_time("")
    assert empty.is_stated is False and empty.worst_case_minutes is None

    prose = parse_review_time("quick")
    assert prose.is_stated is True and prose.worst_case_minutes is None


def test_exceeds_budget_only_when_worst_case_is_over_the_cap():
    assert parse_review_time("5 / 15 / 30").exceeds_budget is False
    assert parse_review_time(f"5 / 15 / {MAX_WORST_CASE_MINUTES}").exceeds_budget is False
    assert parse_review_time(f"5 / 15 / {MAX_WORST_CASE_MINUTES + 1}").exceeds_budget is True


def test_review_time_problems_reports_missing_unparseable_and_over_budget():
    assert review_time_problems("Export CSV", None) == ['"Export CSV": missing Estimated PR Review Time']
    assert review_time_problems("Export CSV", "  ") == ['"Export CSV": missing Estimated PR Review Time']
    assert review_time_problems("Export CSV", "quick") == [
        '"Export CSV": Estimated PR Review Time ("quick") is not a recognizable minutes estimate'
    ]
    over = review_time_problems("Export CSV", "10 / 40 / 90")
    assert len(over) == 1 and "worst case is 90 minutes" in over[0] and "split this story" in over[0]
    assert review_time_problems("Export CSV", "5 / 15 / 30") == []
