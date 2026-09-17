"""Cost — an explicit known/unknown representation, so "this call's price
could not be determined" is never silently recorded as $0.00.

HARD RULE this type exists to enforce: `Cost.unknown()` and `Cost.free()`
are both real, distinct, valid states — a caller must never coerce one
into the other. The Phase 00 architecture baseline found exactly this bug
in the existing (still-preserved-behind-a-flag) legacy path: OpenRouter/
NVIDIA always report cost=0.0, even when a caller has pointed
OPENROUTER_MODEL/NVIDIA_MODEL at a genuinely paid model — this type is
what every NEW ModelGateway adapter uses instead, so that specific gap
cannot recur.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Cost:
    """`is_known=False` implies `amount_usd is None` — enforced in
    `__post_init__` rather than trusted, since this type's entire purpose
    is to make "we don't actually know" impossible to misrepresent as a
    number."""

    is_known: bool
    amount_usd: float | None

    def __post_init__(self) -> None:
        if self.is_known and self.amount_usd is None:
            raise ValueError("Cost.is_known=True requires a non-None amount_usd — use Cost.unknown() for an unknown cost.")
        if not self.is_known and self.amount_usd is not None:
            raise ValueError("Cost.is_known=False must carry amount_usd=None — an unknown cost has no number, not even 0.")
        if self.is_known and self.amount_usd < 0:
            raise ValueError("Cost.amount_usd cannot be negative.")

    @staticmethod
    def of(amount_usd: float) -> "Cost":
        """A real, computed cost — including a real $0.00 for a call that
        genuinely cost nothing (see `free()`, its more self-documenting
        alias for that specific case)."""
        return Cost(is_known=True, amount_usd=amount_usd)

    @staticmethod
    def free() -> "Cost":
        """A call that is genuinely, structurally free — e.g. Ollama
        (runs locally, no metered API) — not "we didn't bother pricing
        it." Distinct spelling from `Cost.of(0.0)` only for readability at
        call sites; behaviorally identical."""
        return Cost(is_known=True, amount_usd=0.0)

    @staticmethod
    def unknown() -> "Cost":
        """The call may have cost real money, but this adapter has no
        reliable way to know how much — e.g. a hosted catalog (OpenRouter)
        where the configured model isn't confirmed free-tier, and no
        per-call cost was returned by the provider itself."""
        return Cost(is_known=False, amount_usd=None)

    def __add__(self, other: "Cost") -> "Cost":
        """Summing costs across a multi-call sequence (e.g. a retry/
        fallback chain, or a loop's several iterations): known + known
        adds normally; anything touching an unknown makes the total
        unknown — a partial number would be misleading, not merely
        imprecise."""
        if self.is_known and other.is_known:
            return Cost.of(self.amount_usd + other.amount_usd)
        return Cost.unknown()

    def to_report_value(self) -> float | None:
        """The one place `Cost` collapses back to a plain float-or-None —
        for a JSON/DB field where `None` unambiguously means "unknown"
        (see app/models/agent.py's AgentRun.cost, already `Float,
        nullable=True` — this shape was always representable, just never
        actually returned for OpenRouter/NVIDIA on the legacy path)."""
        return self.amount_usd if self.is_known else None
