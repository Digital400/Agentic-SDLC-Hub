"""Phase 17: RuntimeCostRouter — "Auto should select the lowest-cost
runtime likely to succeed, while stopping before endless expensive repair
loops."

This is a pure, dependency-free decision engine: given a `RoutingSignals`
snapshot, `RuntimeCostRouter.route()` returns a `RoutingDecision` with the
requested/candidate/selected runtime and model, an estimated cost, and an
explicit rejection reason for every candidate that wasn't picked — never
a silent choice. No network calls, no database access; a caller (not
built in this phase — see docs/architecture/cost-aware-routing.md
"Remaining risks") is responsible for turning a real request into
`RoutingSignals` and persisting the resulting `RoutingDecision` via
app/models/runtime_routing_decision.py.

Nothing in this codebase calls `RuntimeCostRouter` from a live path yet.
`Settings.RUNTIME_COST_ROUTER_ENABLED` (default False) exists for a
future caller to check before doing so; this phase does not flip it or
wire it into apps/web's Phase 14 `resolveAutoSelection()` placeholder
(a different codebase/language, and a genuinely separate integration
task from building the router itself).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ComplexityLevel = Literal["deterministic", "simple", "normal", "complex"]
CostOwner = Literal["company", "user", "local_infrastructure"]


@dataclass
class RoutingSignals:
    """Every input Phase 17 names, gathered into one snapshot. Optional
    fields default to a conservative ("assume the worst / don't escalate")
    value so a caller with partial information never accidentally
    triggers a premium selection by omission."""

    task_type: str
    required_capabilities: list[str] = field(default_factory=list)
    estimated_complexity: ComplexityLevel = "normal"
    repository_size_loc: int | None = None
    expected_changed_file_count: int | None = None
    data_classification: str = "internal"
    security_requires_strong_model: bool = False
    runtime_success_history: dict[str, float] = field(default_factory=dict)  # runtime key -> success rate [0,1]
    user_connection_available: bool = False
    runtime_health: dict[str, bool] = field(default_factory=dict)  # runtime key -> healthy
    queue_capacity_available: bool = True
    project_budget_remaining_usd: float | None = None
    previous_failed_repair_attempts: int = 0
    cost_owner: CostOwner = "company"


@dataclass
class RoutingCandidate:
    runtime: str
    model: str
    estimated_cost_usd: float
    rejected_reason: str | None = None


@dataclass
class RoutingDecision:
    requested_runtime: str
    requested_model: str
    candidates: list[RoutingCandidate]
    selected_runtime: str | None
    selected_model: str | None
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    escalation_reason: str | None
    cost_owner: CostOwner
    stop_for_human: bool
    stop_reason: str | None


# --- Default policy table (Phase 17's own literal list) ---------------------------------------

_LOCAL_MODEL = ("local-runtime", "small-local-model")
_LOW_COST_DOCUMENT_MODEL = ("document-runtime", "standard-low-cost-model")
_OPENCODE_AFFORDABLE = ("opencode", "affordable-coding-model")
_STANDARD_CODING_MODEL = ("opencode", "standard-coding-model")
_PREMIUM_CODING_MODEL = ("codex", "premium-coding-model")

_MAX_FAILED_REPAIR_ATTEMPTS_BEFORE_HUMAN = 2


class RuntimeCostRouter:
    def route(self, signals: RoutingSignals, *, requested_runtime: str = "auto", requested_model: str = "auto") -> RoutingDecision:
        candidates: list[RoutingCandidate] = []

        # "Two failed repair attempts: stop for human decision." — checked
        # first; a human is asked before any candidate is even evaluated.
        if signals.previous_failed_repair_attempts >= _MAX_FAILED_REPAIR_ATTEMPTS_BEFORE_HUMAN:
            return RoutingDecision(
                requested_runtime=requested_runtime, requested_model=requested_model, candidates=[],
                selected_runtime=None, selected_model=None, estimated_cost_usd=None, actual_cost_usd=None,
                escalation_reason=None, cost_owner=signals.cost_owner, stop_for_human=True,
                stop_reason=f"{signals.previous_failed_repair_attempts} failed repair attempts — stopping for human decision rather than retrying further.",
            )

        # "Deterministic task: no LLM."
        if signals.estimated_complexity == "deterministic":
            return RoutingDecision(
                requested_runtime=requested_runtime, requested_model=requested_model, candidates=[],
                selected_runtime="deterministic", selected_model="none", estimated_cost_usd=0.0, actual_cost_usd=None,
                escalation_reason=None, cost_owner=signals.cost_owner, stop_for_human=False, stop_reason=None,
            )

        candidates.extend(self._document_candidates(signals))
        candidates.extend(self._coding_candidates(signals))

        selected = self._first_viable(candidates)
        if selected is None:
            return RoutingDecision(
                requested_runtime=requested_runtime, requested_model=requested_model, candidates=candidates,
                selected_runtime=None, selected_model=None, estimated_cost_usd=None, actual_cost_usd=None,
                escalation_reason=None, cost_owner=signals.cost_owner, stop_for_human=True,
                stop_reason="No candidate runtime is currently viable (all rejected) — stopping for human selection.",
            )

        escalation_reason = self._escalation_reason(signals, selected)
        return RoutingDecision(
            requested_runtime=requested_runtime, requested_model=requested_model, candidates=candidates,
            selected_runtime=selected.runtime, selected_model=selected.model, estimated_cost_usd=selected.estimated_cost_usd,
            actual_cost_usd=None, escalation_reason=escalation_reason, cost_owner=signals.cost_owner,
            stop_for_human=False, stop_reason=None,
        )

    def _document_candidates(self, signals: RoutingSignals) -> list[RoutingCandidate]:
        if "coding" in signals.task_type.lower() or signals.required_capabilities:
            return []  # this is a coding-shaped task; document candidates don't apply.
        if signals.estimated_complexity == "simple":
            runtime, model = _LOCAL_MODEL
        else:
            runtime, model = _LOW_COST_DOCUMENT_MODEL
        return [self._evaluate(signals, runtime, model, base_cost=0.02 if runtime == _LOCAL_MODEL[0] else 0.10)]

    def _coding_candidates(self, signals: RoutingSignals) -> list[RoutingCandidate]:
        if not ("coding" in signals.task_type.lower() or signals.required_capabilities):
            return []
        out: list[RoutingCandidate] = []
        if signals.estimated_complexity in ("simple", "normal"):
            out.append(self._evaluate(signals, *_OPENCODE_AFFORDABLE, base_cost=0.30))
        else:
            # "Complex coding: standard model first unless security policy requires stronger."
            if signals.security_requires_strong_model:
                out.append(self._evaluate(signals, *_PREMIUM_CODING_MODEL, base_cost=2.50))
            else:
                out.append(self._evaluate(signals, *_STANDARD_CODING_MODEL, base_cost=0.80))
                out.append(self._evaluate(signals, *_PREMIUM_CODING_MODEL, base_cost=2.50))
        return out

    def _evaluate(self, signals: RoutingSignals, runtime: str, model: str, *, base_cost: float) -> RoutingCandidate:
        if not signals.runtime_health.get(runtime, True):
            return RoutingCandidate(runtime=runtime, model=model, estimated_cost_usd=base_cost, rejected_reason=f"{runtime} is not currently healthy.")
        if runtime == "local-runtime" and not signals.user_connection_available:
            return RoutingCandidate(runtime=runtime, model=model, estimated_cost_usd=base_cost, rejected_reason="No connected local runtime is available.")
        if not signals.queue_capacity_available:
            return RoutingCandidate(runtime=runtime, model=model, estimated_cost_usd=base_cost, rejected_reason="No queue capacity currently available.")
        if signals.project_budget_remaining_usd is not None and base_cost > signals.project_budget_remaining_usd:
            return RoutingCandidate(runtime=runtime, model=model, estimated_cost_usd=base_cost, rejected_reason=f"Estimated cost ${base_cost:.2f} exceeds remaining project budget ${signals.project_budget_remaining_usd:.2f}.")
        success_rate = signals.runtime_success_history.get(runtime)
        if success_rate is not None and success_rate < 0.5:
            return RoutingCandidate(runtime=runtime, model=model, estimated_cost_usd=base_cost, rejected_reason=f"{runtime}'s historical success rate ({success_rate:.0%}) is below the 50% floor.")
        return RoutingCandidate(runtime=runtime, model=model, estimated_cost_usd=base_cost, rejected_reason=None)

    def _first_viable(self, candidates: list[RoutingCandidate]) -> RoutingCandidate | None:
        for candidate in candidates:
            if candidate.rejected_reason is None:
                return candidate
        return None

    def _escalation_reason(self, signals: RoutingSignals, selected: RoutingCandidate) -> str | None:
        is_premium = selected.runtime == _PREMIUM_CODING_MODEL[0] and selected.model == _PREMIUM_CODING_MODEL[1]
        if not is_premium:
            return None
        if signals.security_requires_strong_model:
            return "Escalated to the premium coding model: security policy requires a stronger model for this task's data classification."
        return "Escalated to the premium coding model: the standard model candidate was rejected (see its own rejected_reason)."
