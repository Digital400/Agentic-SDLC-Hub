"""Runtime Capability Registry (Phase 02).

Users may select a runtime, but the system must verify whether that
runtime can perform the required task safely. This package is that
verification layer: a closed vocabulary of what a runtime can DO
(RuntimeCapability), what KIND of work it does (RuntimeKind) and WHERE it
runs (ExecutionLocation), a registry of concrete adapters
(RuntimeRegistry), and the policy that picks one for a given WorkPacket
(RuntimePolicyEvaluator).

Builds directly on app.agent_runtime (Phase 01) — every contract that
crosses the runtime-selection boundary (RuntimeCapabilityManifest,
WorkPacket, ExecutionResult) lives there, not duplicated here; this
package adds the SELECTION machinery Phase 01 explicitly deferred ("do
not connect an external runtime yet").

STATUS: additive, not connected — same phase-boundary discipline every
prior phase in this arc established (see app.model_gateway,
app.prompt_compiler's own "not wired into any route yet" notes). No
existing route or service is repointed at this package by this phase.
app.services.coding_runtimes.build_coding_runtime_registry (Phase 08) is
a narrower, purpose-built declaration that predates this package and is
NOT migrated onto it here — Phase 02 was inserted after Phase 08 already
shipped (see this package's own git history / the Phase 00-08 checkpoint
commit); reconciling the two is out of this phase's stated scope
("implement only the requested phase").

Legacy adapters (LegacyDocumentRuntimeAdapter, LegacyCodingRuntimeAdapter
— see legacy_adapter.py) wrap today's real generation paths unmodified,
satisfying this phase's "route the existing harness through
LegacyRuntimeAdapter by default; do not change existing external
behavior" — but nothing constructs or registers one outside this
package's own tests yet, for the same "additive, not connected" reason.
"""

from app.agent_runtime.capability import RuntimeCapability, RuntimeCapabilityManifest
from app.coding_runtime.base import CodingRuntimeAdapter, DocumentRuntimeAdapter, RuntimeAdapterError
from app.coding_runtime.enums import ExecutionLocation, RuntimeKind
from app.coding_runtime.fake_adapter import FakeRuntimeAdapter
from app.coding_runtime.legacy_adapter import LegacyCodingRuntimeAdapter, LegacyDocumentRuntimeAdapter
from app.coding_runtime.manifest import RuntimeAvailability, RuntimeConnection, RuntimeDefinition, RuntimeHealth
from app.coding_runtime.registry import RuntimeRegistry, RuntimeRegistryError
from app.coding_runtime.selection import RuntimePolicyEvaluator, RuntimeSelectionDecision, RuntimeSelectionRequest

__all__ = [
    "CodingRuntimeAdapter",
    "DocumentRuntimeAdapter",
    "ExecutionLocation",
    "FakeRuntimeAdapter",
    "LegacyCodingRuntimeAdapter",
    "LegacyDocumentRuntimeAdapter",
    "RuntimeAdapterError",
    "RuntimeAvailability",
    "RuntimeCapability",
    "RuntimeCapabilityManifest",
    "RuntimeConnection",
    "RuntimeDefinition",
    "RuntimeHealth",
    "RuntimeKind",
    "RuntimePolicyEvaluator",
    "RuntimeRegistry",
    "RuntimeRegistryError",
    "RuntimeSelectionDecision",
    "RuntimeSelectionRequest",
]
