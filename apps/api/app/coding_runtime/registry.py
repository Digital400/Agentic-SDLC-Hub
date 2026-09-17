"""RuntimeRegistry — where every known runtime adapter is registered and
selected from. See app/coding_runtime/__init__.py.
"""

from __future__ import annotations

from app.agent_runtime import WorkPacketTaskType
from app.agent_runtime.capability import RuntimeCapability
from app.coding_runtime.base import CodingRuntimeAdapter, DocumentRuntimeAdapter
from app.coding_runtime.manifest import RuntimeAvailability, RuntimeConnection, RuntimeHealth
from app.coding_runtime.selection import RuntimePolicyEvaluator, RuntimeSelectionDecision, RuntimeSelectionRequest

RuntimeAdapter = CodingRuntimeAdapter | DocumentRuntimeAdapter

# Which capabilities a task_type needs, at minimum, before any runtime is
# even considered a candidate — a superset of "the runtime kind must
# match" (see selection.py's own kind check). Every task_type not listed
# here needs no capability beyond kind-matching (a plain document draft).
_REQUIRED_CAPABILITIES_BY_TASK_TYPE: dict[WorkPacketTaskType, list[RuntimeCapability]] = {
    WorkPacketTaskType.IMPLEMENT_STORY: [RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION],
    WorkPacketTaskType.PR_REVIEW: [RuntimeCapability.REPOSITORY_READ],
}


class RuntimeRegistryError(Exception):
    """Raised for a registry-usage mistake (registering the same name
    twice, looking up a name that was never registered) — never for a
    runtime's own execution failure, which is RuntimeAdapterError's job."""


class RuntimeRegistry:
    """Holds every registered adapter plus this phase's dynamic
    bookkeeping about each (availability, last-known health, per-actor
    connection state), and answers "which runtime should run this
    WorkPacket" via RuntimePolicyEvaluator.

    Registration order is this registry's own priority order — see
    RuntimePolicyEvaluator.evaluate's "first surviving candidate" rule
    when no user preference is given, and select()'s own docstring.
    """

    def __init__(self, *, evaluator: RuntimePolicyEvaluator | None = None) -> None:
        self._adapters: dict[str, RuntimeAdapter] = {}
        self._availability: dict[str, RuntimeAvailability] = {}
        self._evaluator = evaluator or RuntimePolicyEvaluator()

    def register(self, adapter: RuntimeAdapter, *, available: bool = True, reason: str | None = None) -> None:
        name = adapter.definition.name
        if name in self._adapters:
            raise RuntimeRegistryError(f"A runtime named {name!r} is already registered.")
        self._adapters[name] = adapter
        self._availability[name] = RuntimeAvailability(runtime_name=name, available=available, reason=reason)

    def set_available(self, name: str, *, available: bool, reason: str | None = None) -> None:
        self._require(name)
        self._availability[name] = RuntimeAvailability(runtime_name=name, available=available, reason=reason)

    def get(self, name: str) -> RuntimeAdapter:
        self._require(name)
        return self._adapters[name]

    def list_definitions(self):
        return [adapter.definition for adapter in self._adapters.values()]

    def check_health(self, name: str) -> RuntimeHealth:
        self._require(name)
        return self._adapters[name].check_health()

    def select(
        self,
        request: RuntimeSelectionRequest,
        *,
        connections: dict[str, RuntimeConnection] | None = None,
    ) -> RuntimeSelectionDecision:
        """Filters registered runtimes down to ones that are: registered
        AND available AND (no `connections` given, OR connected for the
        requesting actor) — in that order — then hands the survivors to
        RuntimePolicyEvaluator for the rest of this phase's policy
        (kind/capability/allowlist/execution-location). `connections` is
        optional because not every task_type needs an actor-specific
        connection (a company-managed sandbox runtime has none to check);
        callers that DO need it (Phase 13's Developer Local Bridge) pass
        it explicitly."""
        candidates = []
        rejected: dict[str, str] = {}
        for name, adapter in self._adapters.items():
            availability = self._availability[name]
            if not availability.available:
                rejected[name] = availability.reason or "Not currently available."
                continue
            if connections is not None:
                connection = connections.get(name)
                if connection is None or not connection.connected:
                    rejected[name] = "No connection for this actor."
                    continue
            candidates.append(adapter.definition)

        required = list(request.required_capabilities) or _REQUIRED_CAPABILITIES_BY_TASK_TYPE.get(request.task_type, [])
        if required != request.required_capabilities:
            request = RuntimeSelectionRequest(
                packet=request.packet, task_type=request.task_type, required_capabilities=required,
                user_preference=request.user_preference, company_allowlist=request.company_allowlist,
                project_allowed_execution_locations=request.project_allowed_execution_locations,
                data_classification=request.data_classification, max_cost_usd=request.max_cost_usd,
            )

        decision = self._evaluator.evaluate(request, candidates)
        # Merge in runtimes rejected before ever reaching the evaluator
        # (unavailable / unconnected) — RuntimeSelectionDecision.rejected
        # is meant to explain every candidate's fate, not just the ones
        # that made it to policy evaluation.
        decision.rejected = {**rejected, **decision.rejected}
        return decision

    def _require(self, name: str) -> None:
        if name not in self._adapters:
            raise RuntimeRegistryError(f"No runtime named {name!r} is registered.")
