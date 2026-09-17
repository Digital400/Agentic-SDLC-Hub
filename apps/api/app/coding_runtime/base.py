"""CodingRuntimeAdapter / DocumentRuntimeAdapter — the interface every
concrete runtime implements. See app/coding_runtime/__init__.py.

Mirrors app.model_gateway.base.ModelGateway's exact pattern: an abc.ABC
interface with the narrowest possible surface, so "a runtime can only do
what this interface exposes" is structural, not a convention. A CODING-
kind runtime (RuntimeKind.CODING) implements CodingRuntimeAdapter; a
DOCUMENT-kind runtime (RuntimeKind.DOCUMENT) implements
DocumentRuntimeAdapter. Both consume a WorkPacket and return an
ExecutionResult — see app.agent_runtime — so a caller never needs to
know which concrete adapter it holds beyond that kind split.
"""

from __future__ import annotations

import abc

from app.agent_runtime import ExecutionResult, WorkPacket
from app.coding_runtime.manifest import RuntimeDefinition, RuntimeHealth


class RuntimeAdapterError(Exception):
    """Raised when an adapter's own execute() call cannot even attempt
    the work (e.g. the runtime process failed to start) — distinct from a
    normal FAILED ExecutionResult, which is the expected, typed outcome
    for a WorkPacket the runtime genuinely tried and could not complete.
    Mirrors app.model_gateway.base.ModelGatewayError's role one level up."""


class CodingRuntimeAdapter(abc.ABC):
    """One coding-capable runtime (RuntimeKind.CODING) — the interface
    every concrete adapter (LegacyRuntimeAdapter, FakeRuntimeAdapter, and
    eventually Phase 08's OpenCodeRuntimeAdapter, Phase 11's
    AcpCodingRuntimeAdapter, Phase 12's premium adapters) implements.
    """

    definition: RuntimeDefinition

    @abc.abstractmethod
    def execute(self, packet: WorkPacket) -> ExecutionResult:
        """Runs `packet` to completion (or CLARIFICATION_REQUIRED /
        WAITING_APPROVAL / FAILED) and returns a real ExecutionResult.
        Raises RuntimeAdapterError only when the runtime itself could not
        even attempt the work — never for a normal in-band failure, which
        belongs in ExecutionResult.failure instead."""

    @abc.abstractmethod
    def check_health(self) -> RuntimeHealth:
        """A single, real (not cached) liveness probe. Callers should go
        through RuntimeRegistry.check_health (which may cache) instead of
        calling this directly on every request — same reasoning as
        ModelGateway.check_health's own docstring."""


class DocumentRuntimeAdapter(abc.ABC):
    """One document-drafting runtime (RuntimeKind.DOCUMENT) — the
    markdown/artifact-drafting sibling of CodingRuntimeAdapter. Today's
    entire ai_generation.py path (every project-level DRAFT/IMPROVE/
    VALIDATE run, every story_lld_agent.py call) is exactly this kind of
    runtime; LegacyRuntimeAdapter's document-side implementation wraps it
    unmodified (see legacy_adapter.py)."""

    definition: RuntimeDefinition

    @abc.abstractmethod
    def execute(self, packet: WorkPacket) -> ExecutionResult:
        """Same contract as CodingRuntimeAdapter.execute — see there."""

    @abc.abstractmethod
    def check_health(self) -> RuntimeHealth:
        """Same contract as CodingRuntimeAdapter.check_health — see there."""
