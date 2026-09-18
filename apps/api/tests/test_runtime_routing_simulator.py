"""Phase 17: offline routing simulator — see
app/services/runtime_routing_simulator.py.
"""

import pytest

from app.services.runtime_cost_router import RoutingSignals
from app.services.runtime_routing_simulator import simulate_routing


class TestSimulateRouting:
    def test_reports_a_decision_per_scenario(self):
        scenarios = [
            RoutingSignals(task_type="document", estimated_complexity="simple", user_connection_available=True),
            RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="normal"),
        ]
        summary = simulate_routing(scenarios)
        assert summary.scenario_count == 2
        assert len(summary.decisions) == 2

    def test_counts_stopped_for_human_scenarios(self):
        scenarios = [
            RoutingSignals(task_type="coding", previous_failed_repair_attempts=3),
            RoutingSignals(task_type="document", estimated_complexity="normal"),
        ]
        summary = simulate_routing(scenarios)
        assert summary.stopped_for_human_count == 1

    def test_counts_premium_selections(self):
        scenarios = [
            RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="complex", security_requires_strong_model=True),
            RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="simple"),
        ]
        summary = simulate_routing(scenarios)
        assert summary.premium_selected_count == 1

    def test_computes_total_and_average_estimated_cost(self):
        scenarios = [
            RoutingSignals(task_type="document", estimated_complexity="normal"),
            RoutingSignals(task_type="document", estimated_complexity="normal"),
        ]
        summary = simulate_routing(scenarios)
        assert summary.total_estimated_cost_usd == pytest.approx(0.20)
        assert summary.average_estimated_cost_usd == pytest.approx(0.10)

    def test_average_cost_is_none_when_every_scenario_stops_for_human(self):
        scenarios = [RoutingSignals(task_type="coding", previous_failed_repair_attempts=5)]
        summary = simulate_routing(scenarios)
        assert summary.average_estimated_cost_usd is None

    def test_empty_scenario_list_produces_a_valid_zeroed_summary(self):
        summary = simulate_routing([])
        assert summary.scenario_count == 0
        assert summary.average_estimated_cost_usd is None
        assert summary.total_estimated_cost_usd == 0.0
