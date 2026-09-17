"""RuntimeSelectionRequest/Decision and RuntimePolicyEvaluator — deciding
which registered runtime should execute a given WorkPacket. See
app/coding_runtime/__init__.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent_runtime import WorkPacket, WorkPacketTaskType
from app.agent_runtime.capability import RuntimeCapability
from app.coding_runtime.enums import ExecutionLocation, RuntimeKind
from app.coding_runtime.manifest import RuntimeDefinition


@dataclass
class RuntimeSelectionRequest:
    """Everything a RuntimePolicyEvaluator needs to pick a runtime for
    one WorkPacket. `required_capabilities` is normally derived from
    `task_type` by the caller (see RuntimeRegistry.select) rather than
    hand-built — this dataclass just carries the already-derived list so
    the evaluator itself stays capability-driven, not task-type-driven,
    and works unmodified as new task types are added."""

    packet: WorkPacket
    task_type: WorkPacketTaskType
    required_capabilities: list[RuntimeCapability]
    user_preference: str | None = None
    company_allowlist: list[str] | None = None
    project_allowed_execution_locations: list[ExecutionLocation] | None = None
    data_classification: str | None = None
    max_cost_usd: float | None = None


@dataclass
class RuntimeSelectionDecision:
    """What RuntimePolicyEvaluator decided, and — critically — WHY, for
    both the winning runtime and (if selection fell back from the user's
    own preference) the reason it didn't use that preference. Persisted
    verbatim by whatever route/service calls the evaluator, per this
    phase's "Persist: requested runtime, selected runtime, ... selection
    reason, fallback reason" requirement."""

    requested_runtime: str | None
    selected_runtime: str | None
    reason: str
    fallback_reason: str | None = None
    rejected: dict[str, str] = field(default_factory=dict)


class RuntimePolicyEvaluator:
    """Applies this phase's selection policy, in order, to a
    RuntimeSelectionRequest against a list of candidate RuntimeDefinition
    rows (already filtered to ones a RuntimeAvailability/RuntimeHealth/
    RuntimeConnection check says are usable right now — see
    RuntimeRegistry.select, which does that filtering before calling
    this). Every rejected candidate's reason is recorded, not just the
    winner's — see RuntimeSelectionDecision.rejected."""

    def evaluate(self, request: RuntimeSelectionRequest, candidates: list[RuntimeDefinition]) -> RuntimeSelectionDecision:
        rejected: dict[str, str] = {}
        survivors: list[RuntimeDefinition] = []

        for candidate in candidates:
            reason = self._first_rejection_reason(request, candidate)
            if reason is not None:
                rejected[candidate.name] = reason
            else:
                survivors.append(candidate)

        if not survivors:
            return RuntimeSelectionDecision(
                requested_runtime=request.user_preference,
                selected_runtime=None,
                reason="No candidate runtime satisfied every requirement.",
                rejected=rejected,
            )

        # User preference wins if it survived every filter; otherwise the
        # first surviving candidate (candidates are expected to already
        # be given in the caller's own priority order — RuntimeRegistry
        # registers runtimes in a fixed, documented order) is selected,
        # with an explicit fallback_reason recorded.
        if request.user_preference is not None:
            preferred = next((c for c in survivors if c.name == request.user_preference), None)
            if preferred is not None:
                return RuntimeSelectionDecision(
                    requested_runtime=request.user_preference, selected_runtime=preferred.name,
                    reason="User preference satisfied every requirement.", rejected=rejected,
                )
            fallback = survivors[0]
            fallback_reason = rejected.get(request.user_preference, "Preferred runtime was not among the candidates evaluated.")
            return RuntimeSelectionDecision(
                requested_runtime=request.user_preference, selected_runtime=fallback.name,
                reason=f"Fell back to {fallback.name!r} — user preference unavailable.",
                fallback_reason=fallback_reason, rejected=rejected,
            )

        winner = survivors[0]
        return RuntimeSelectionDecision(
            requested_runtime=None, selected_runtime=winner.name,
            reason=f"Selected {winner.name!r} — first candidate satisfying every requirement (no user preference given).",
            rejected=rejected,
        )

    def _first_rejection_reason(self, request: RuntimeSelectionRequest, candidate: RuntimeDefinition) -> str | None:
        expected_kind = RuntimeKind.CODING if request.task_type in _CODING_TASK_TYPES else RuntimeKind.DOCUMENT
        if candidate.kind != expected_kind:
            return f"Runtime kind {candidate.kind.value} does not match the {expected_kind.value} kind {request.task_type.value} requires."

        missing = [c for c in request.required_capabilities if c not in candidate.capability.capabilities]
        if missing:
            return "Missing required capabilities: " + ", ".join(c.value for c in missing)

        if request.company_allowlist is not None and candidate.name not in request.company_allowlist:
            return "Not on the company runtime allowlist for this project."

        if (
            request.project_allowed_execution_locations is not None
            and candidate.execution_location not in request.project_allowed_execution_locations
        ):
            return f"Execution location {candidate.execution_location.value} is not permitted for this project."

        return None


# Task types this codebase's Phase 00 baseline classified as code-writing
# (IMPLEMENT_STORY, PR_REVIEW) vs. document-drafting (everything else) —
# mirrors app.agent_runtime.enums.WorkPacketTaskType's own docstring
# mapping. TEST_STORY is document-kind here: today's Testing Agent (Phase
# 00 baseline section 8) is a reasoning-based assessment, not real
# execution — Phase 09 is where that changes.
_CODING_TASK_TYPES = {WorkPacketTaskType.IMPLEMENT_STORY, WorkPacketTaskType.PR_REVIEW}
