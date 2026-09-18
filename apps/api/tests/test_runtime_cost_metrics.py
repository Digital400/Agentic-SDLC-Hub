"""Phase 17: persisted routing decisions and the six cost/quality
measurements — see app/services/runtime_cost_metrics.py.
"""

import pytest

from app.services.runtime_cost_metrics import (
    cache_savings,
    cost_per_accepted_patch,
    cost_per_approved_artifact,
    cost_per_merged_story,
    failed_attempt_cost,
    persist_routing_decision,
    premium_runtime_percentage,
)
from app.services.runtime_cost_router import RoutingCandidate, RoutingDecision


def _decision(**overrides) -> RoutingDecision:
    defaults = dict(
        requested_runtime="auto", requested_model="auto",
        candidates=[RoutingCandidate(runtime="opencode", model="affordable-coding-model", estimated_cost_usd=0.3, rejected_reason=None)],
        selected_runtime="opencode", selected_model="affordable-coding-model", estimated_cost_usd=0.3, actual_cost_usd=0.28,
        escalation_reason=None, cost_owner="company", stop_for_human=False, stop_reason=None,
    )
    defaults.update(overrides)
    return RoutingDecision(**defaults)


class TestPersistRoutingDecision:
    def test_persists_every_recorded_field(self, db, project):
        row = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision())
        db.commit()
        assert row.selected_runtime == "opencode"
        assert row.actual_cost_usd == 0.28
        assert row.candidates_json[0]["runtime"] == "opencode"

    def test_flags_is_premium_correctly(self, db, project):
        premium_decision = _decision(selected_runtime="codex", selected_model="premium-coding-model")
        row = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=premium_decision)
        db.commit()
        assert row.is_premium is True

    def test_non_premium_selection_is_not_flagged_premium(self, db, project):
        row = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision())
        db.commit()
        assert row.is_premium is False


class TestCostPerOutcomeMetrics:
    def test_returns_none_when_no_decisions_have_the_outcome_flag_set(self, db, project):
        persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision())
        db.commit()
        assert cost_per_approved_artifact(db, project.id) is None
        assert cost_per_accepted_patch(db, project.id) is None
        assert cost_per_merged_story(db, project.id) is None

    def test_averages_actual_cost_across_flagged_decisions(self, db, project):
        row1 = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(actual_cost_usd=0.2))
        row2 = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(actual_cost_usd=0.4))
        row1.patch_accepted = True
        row2.patch_accepted = True
        db.commit()
        assert cost_per_accepted_patch(db, project.id) == pytest.approx(0.3)


class TestPremiumRuntimePercentage:
    def test_returns_none_with_zero_decisions(self, db, project):
        assert premium_runtime_percentage(db, project.id) is None

    def test_computes_the_correct_percentage(self, db, project):
        persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision())
        persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(selected_runtime="codex", selected_model="premium-coding-model"))
        db.commit()
        assert premium_runtime_percentage(db, project.id) == 50.0

    def test_excludes_stopped_for_human_decisions_from_the_denominator(self, db, project):
        persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(stop_for_human=True, selected_runtime=None, selected_model=None))
        persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(selected_runtime="codex", selected_model="premium-coding-model"))
        db.commit()
        assert premium_runtime_percentage(db, project.id) == 100.0


class TestCacheSavingsAndFailedAttemptCost:
    def test_cache_savings_defaults_to_zero_not_none(self, db, project):
        assert cache_savings(db, project.id) == 0.0

    def test_failed_attempt_cost_sums_only_failed_executions(self, db, project):
        failed = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(actual_cost_usd=1.5))
        ok = persist_routing_decision(db, project_id=project.id, task_type="coding", decision=_decision(actual_cost_usd=0.5))
        failed.execution_failed = True
        db.commit()
        assert failed_attempt_cost(db, project.id) == 1.5
