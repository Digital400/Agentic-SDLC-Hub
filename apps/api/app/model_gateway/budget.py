"""Company and project spend budgets — this phase's "Support company and
project budgets" requirement.

SCOPE: this is the tracking/enforcement PRIMITIVE (an interface plus one
in-process reference implementation), not a persisted, API-exposed budget
management feature — no new database table or route is introduced here.
The Phase 00 architecture baseline confirmed this codebase currently has
NO spend-limit enforcement of any kind (only context-window budgeting);
this module is the seam a future phase would wire a persisted
implementation into (see BudgetTracker — swap InMemoryBudgetTracker for a
DB-backed one behind the same interface, same pattern every prior phase's
"replace this seam later" precedent already established).
"""

from __future__ import annotations

import abc
import enum
import threading

from app.model_gateway.cost import Cost


class BudgetScope(str, enum.Enum):
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"


class BudgetExceededError(Exception):
    """Raised by BudgetTracker.check_and_reserve when spending `cost`
    against `scope`/`key` would exceed `max_cost_usd`. An UNKNOWN cost
    (see app/model_gateway/cost.py) never raises this — a tracker cannot
    refuse spend it cannot measure; that's a monitoring/alerting concern,
    not a hard stop this primitive enforces itself."""

    def __init__(self, scope: BudgetScope, key: str, spent_usd: float, max_usd: float):
        super().__init__(f"{scope.value} budget '{key}' would exceed its ${max_usd:.2f} cap (already spent ${spent_usd:.2f}).")
        self.scope = scope
        self.key = key
        self.spent_usd = spent_usd
        self.max_usd = max_usd


class BudgetTracker(abc.ABC):
    @abc.abstractmethod
    def check_and_reserve(self, *, scope: BudgetScope, key: str, cost: Cost, max_cost_usd: float | None) -> None:
        """Raises BudgetExceededError if recording `cost` against this
        scope/key would exceed `max_cost_usd` (a no-op ceiling when None).
        Records the spend either way (even a rejected attempt still
        reflects real usage already incurred by the call that produced
        this Cost — this is called AFTER a call completes, not before, so
        there is no "un-spend" case to handle)."""

    @abc.abstractmethod
    def spent_usd(self, *, scope: BudgetScope, key: str) -> float:
        """Total known spend recorded for this scope/key so far. Costs
        recorded as unknown never contribute a number here — see
        unknown_call_count for that signal instead."""

    @abc.abstractmethod
    def unknown_call_count(self, *, scope: BudgetScope, key: str) -> int:
        """How many calls against this scope/key had an unknown cost —
        the honest signal that spent_usd() is a FLOOR, not the true
        total, whenever this is non-zero."""


class InMemoryBudgetTracker(BudgetTracker):
    """Process-local reference implementation — resets on restart, not
    shared across processes. Sufficient for a single-process deployment
    or for tests; a real multi-worker deployment needs a shared-storage
    implementation behind this same interface (see module docstring)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._spent: dict[tuple[str, str], float] = {}
        self._unknown_counts: dict[tuple[str, str], int] = {}

    def check_and_reserve(self, *, scope: BudgetScope, key: str, cost: Cost, max_cost_usd: float | None) -> None:
        cache_key = (scope.value, key)
        with self._lock:
            if not cost.is_known:
                self._unknown_counts[cache_key] = self._unknown_counts.get(cache_key, 0) + 1
                return  # never blocks on an unmeasurable cost — see class docstring

            new_total = self._spent.get(cache_key, 0.0) + cost.amount_usd
            if max_cost_usd is not None and new_total > max_cost_usd:
                raise BudgetExceededError(scope, key, spent_usd=self._spent.get(cache_key, 0.0), max_usd=max_cost_usd)
            self._spent[cache_key] = new_total

    def spent_usd(self, *, scope: BudgetScope, key: str) -> float:
        with self._lock:
            return self._spent.get((scope.value, key), 0.0)

    def unknown_call_count(self, *, scope: BudgetScope, key: str) -> int:
        with self._lock:
            return self._unknown_counts.get((scope.value, key), 0)
