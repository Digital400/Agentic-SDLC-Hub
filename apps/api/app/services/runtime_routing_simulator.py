"""Phase 17: "Add an offline routing simulator before enabling Auto in
production."

Nothing in this codebase enables an "Auto" runtime selection in
production yet — apps/web's Phase 14 `resolveAutoSelection()` is itself
an explicitly disclosed placeholder, and no backend caller invokes
RuntimeCostRouter from a live path (see this router's own module
docstring). This simulator is the artifact this phase's own instructions
require to exist BEFORE such a switch is ever flipped — it does not
itself flip one.

Runs a batch of `RoutingSignals` scenarios through `RuntimeCostRouter`
entirely in memory (no database, no network) and reports an aggregate
summary: how many scenarios would stop for a human, the premium-selection
percentage, and total/average estimated cost — the same shape a
maintainer would want to sanity-check before trusting Auto against real
traffic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.runtime_cost_router import RoutingDecision, RoutingSignals, RuntimeCostRouter, _PREMIUM_CODING_MODEL


@dataclass
class SimulationSummary:
    scenario_count: int
    stopped_for_human_count: int
    premium_selected_count: int
    total_estimated_cost_usd: float
    average_estimated_cost_usd: float | None
    decisions: list[RoutingDecision]


def simulate_routing(scenarios: list[RoutingSignals]) -> SimulationSummary:
    router = RuntimeCostRouter()
    decisions = [router.route(scenario) for scenario in scenarios]

    stopped = sum(1 for d in decisions if d.stop_for_human)
    premium = sum(1 for d in decisions if d.selected_runtime == _PREMIUM_CODING_MODEL[0] and d.selected_model == _PREMIUM_CODING_MODEL[1])
    costed = [d.estimated_cost_usd for d in decisions if d.estimated_cost_usd is not None]
    total_cost = sum(costed)

    return SimulationSummary(
        scenario_count=len(scenarios), stopped_for_human_count=stopped, premium_selected_count=premium,
        total_estimated_cost_usd=total_cost, average_estimated_cost_usd=(total_cost / len(costed)) if costed else None,
        decisions=decisions,
    )
