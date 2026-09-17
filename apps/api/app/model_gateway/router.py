"""PolicyDrivenRouter — applies a ModelPolicy's default_alias/
fallback_aliases/max_calls/timeout/max_cost_usd to one ModelGateway
adapter, so a caller gets policy-governed retry/fallback behavior without
each adapter having to implement it itself.

This module does NOT interpret escalation_conditions or
data_region_restriction — see ModelPolicy's own field docstrings: those
are declarative data a caller (e.g. loop_engine.py's own repair-vs-escalate
decision, or a deployment-specific region-aware provider selector) is
expected to read and act on, matching how this codebase's Phase 01
BudgetPolicy/ScopePolicy are also declarative-only, enforced by whoever
consumes them rather than by the contract itself.
"""

from __future__ import annotations

from dataclasses import replace

from app.model_gateway.base import GatewayRequest, GatewayResponse, ModelGateway, ModelGatewayError
from app.model_gateway.budget import BudgetScope, BudgetTracker
from app.model_gateway.policy import ModelPolicy


class PolicyExhaustedError(ModelGatewayError):
    """Every alias in the policy's default+fallback chain failed (or the
    policy's max_calls ceiling was reached first) — carries every
    underlying error for a caller that wants the full picture, not just
    the last one."""

    def __init__(self, attempts: list[tuple[str, Exception]]):
        summary = "; ".join(f"{alias}: {exc}" for alias, exc in attempts)
        super().__init__(f"PolicyDrivenRouter exhausted every alias without a successful call — {summary}")
        self.attempts = attempts


class PolicyDrivenRouter:
    def __init__(self, gateway: ModelGateway, policy: ModelPolicy, *, budget_tracker: BudgetTracker | None = None, budget_scope: BudgetScope = BudgetScope.PROJECT, budget_key: str = "default"):
        self._gateway = gateway
        self._policy = policy
        self._budget_tracker = budget_tracker
        self._budget_scope = budget_scope
        self._budget_key = budget_key

    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """Tries `policy.default_alias`, then each of
        `policy.fallback_aliases` in order, stopping at the first success
        or at `policy.max_calls` attempts, whichever comes first (a policy
        with `max_calls=1` never tries a fallback at all, by design — the
        ceiling applies to the WHOLE chain, not per-alias). Every attempt
        uses `policy.timeout_seconds`/`policy.temperature` unless the
        caller's own request already set them explicitly.
        """
        aliases_to_try = [self._policy.default_alias, *self._policy.fallback_aliases]
        if self._policy.max_calls is not None:
            aliases_to_try = aliases_to_try[: self._policy.max_calls]

        attempts: list[tuple[str, Exception]] = []
        for alias in aliases_to_try:
            attempt_request = replace(
                request,
                alias=alias,
                timeout_seconds=request.timeout_seconds or self._policy.timeout_seconds,
                temperature=request.temperature if request.temperature is not None else self._policy.temperature,
                structured_output_required=request.structured_output_required or self._policy.structured_output_required,
            )
            try:
                response = self._gateway.generate(attempt_request)
            except ModelGatewayError as exc:
                attempts.append((alias.value, exc))
                continue

            if self._budget_tracker is not None:
                # Recorded even on the call that ultimately satisfies the
                # request — see BudgetTracker.check_and_reserve's own
                # docstring for why this never "un-spends" a prior attempt's
                # cost: every attempt, successful or not, already consumed
                # real provider capacity/spend by the time it returns.
                self._budget_tracker.check_and_reserve(
                    scope=self._budget_scope, key=self._budget_key, cost=response.cost, max_cost_usd=self._policy.max_cost_usd,
                )
            return response

        raise PolicyExhaustedError(attempts)
