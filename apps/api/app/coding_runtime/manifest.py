"""RuntimeDefinition — one runtime's static registry entry — plus the
live/dynamic state types (RuntimeAvailability, RuntimeHealth,
RuntimeConnection) a RuntimeRegistry tracks alongside it. See
app/coding_runtime/__init__.py.

Plain dataclasses, not app.agent_runtime Pydantic contracts: these types
never cross the vendor-neutral wire boundary that package's five hard
rules govern (see app.agent_runtime's module docstring) — they're this
application's own bookkeeping about runtimes it knows how to invoke,
mirroring app.model_gateway.base's ProviderHealth/GatewayResponse split
between wire contracts (app.agent_runtime) and live adapter state
(plain dataclasses) for the exact same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.agent_runtime.capability import RuntimeCapabilityManifest
from app.coding_runtime.enums import ExecutionLocation, RuntimeKind


@dataclass(frozen=True)
class RuntimeDefinition:
    """One runtime's static registry entry — what it is, what kind of
    work it does, where it runs, and what it declares it can do. Kept
    immutable (frozen) since a runtime's own identity doesn't change at
    runtime; RuntimeAvailability/RuntimeHealth (below) are the mutable,
    point-in-time facts about it."""

    name: str
    kind: RuntimeKind
    execution_location: ExecutionLocation
    capability: RuntimeCapabilityManifest
    description: str = ""


@dataclass
class RuntimeAvailability:
    """Whether a runtime can be selected RIGHT NOW — distinct from
    RuntimeHealth (a liveness probe) because a runtime can be perfectly
    healthy and still unavailable (disabled by an operator, no connection
    for this org/user, over its concurrency limit)."""

    runtime_name: str
    available: bool
    reason: str | None = None


@dataclass
class RuntimeHealth:
    """A point-in-time liveness probe result — mirrors
    app.model_gateway.base.ProviderHealth's exact shape and role,
    one level up (a runtime, not a single model provider)."""

    runtime_name: str
    healthy: bool
    checked_at: datetime
    detail: str | None = None


@dataclass
class RuntimeConnection:
    """Whether a specific user/org actually has a usable connection to a
    runtime (e.g. an authenticated Developer Local Bridge session, Phase
    13; a company-managed sandbox credential, Phase 07's credential
    broker) — distinct from RuntimeAvailability, which is runtime-wide,
    not actor-specific. `connected` alone gates selection; every other
    field here is informational.

    SECURITY: never holds a credential/token value itself (see Common
    Instruction rule 8 and app.agent_runtime's hard rule 4) — only the
    fact that a connection exists and non-secret bookkeeping about it.
    """

    runtime_name: str
    actor_user_id: str | None
    connected: bool
    connected_at: datetime | None = None
    detail: str | None = None
