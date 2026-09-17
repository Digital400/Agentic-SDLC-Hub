"""FakeRuntimeAdapter — a deterministic, in-memory test double for both
CodingRuntimeAdapter and DocumentRuntimeAdapter. See
app/coding_runtime/__init__.py.

Mirrors app.model_gateway.fake_gateway.FakeModelGateway's exact role:
tests that need "a runtime" without ever touching a real one (legacy or
external) construct this instead — no monkeypatching, no network, fully
scriptable per-test.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent_runtime import ExecutionResult, UsageEvidence, WorkPacket
from app.agent_runtime.capability import RuntimeCapability, RuntimeCapabilityManifest
from app.agent_runtime.enums import ExecutionState
from app.coding_runtime.base import CodingRuntimeAdapter, DocumentRuntimeAdapter, RuntimeAdapterError
from app.coding_runtime.enums import ExecutionLocation, RuntimeKind
from app.coding_runtime.manifest import RuntimeDefinition, RuntimeHealth


class FakeRuntimeAdapter(CodingRuntimeAdapter, DocumentRuntimeAdapter):
    """Implements both interfaces (their method shapes are identical by
    design — see base.py) so one fake serves either kind of conformance
    test. `scripted_result`/`raise_error`/`healthy` are set per-test;
    `calls` records every packet actually passed to `execute` for
    assertion, the same recording-fake pattern this codebase's own test
    suite already uses throughout (e.g. tests/test_pull_request_creation.py's
    _mock_github)."""

    def __init__(
        self,
        *,
        name: str = "fake",
        kind: RuntimeKind = RuntimeKind.CODING,
        capabilities: list[RuntimeCapability] | None = None,
        execution_location: ExecutionLocation = ExecutionLocation.COMPANY_SANDBOX,
        scripted_result: ExecutionResult | None = None,
        raise_error: Exception | None = None,
        healthy: bool = True,
    ) -> None:
        self.definition = RuntimeDefinition(
            name=name, kind=kind, execution_location=execution_location,
            description="Deterministic in-memory test double — see module docstring.",
            capability=RuntimeCapabilityManifest(
                runtime_name=name, max_context_tokens=32000, max_output_tokens=16000,
                supports_structured_output=True, supports_streaming=False,
                capabilities=capabilities if capabilities is not None else list(RuntimeCapability),
            ),
        )
        self._scripted_result = scripted_result
        self._raise_error = raise_error
        self._healthy = healthy
        self.calls: list[WorkPacket] = []

    def execute(self, packet: WorkPacket) -> ExecutionResult:
        self.calls.append(packet)
        if self._raise_error is not None:
            raise self._raise_error
        if self._scripted_result is not None:
            return self._scripted_result
        return ExecutionResult(
            result_id=packet.packet_id, packet_id=packet.packet_id, state=ExecutionState.COMPLETED,
            summary=f"Fake runtime {self.definition.name!r} completed {packet.task_type.value}.",
            usage=UsageEvidence(llm_calls_made=1),
            started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
        )

    def check_health(self) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_name=self.definition.name, healthy=self._healthy, checked_at=datetime.now(timezone.utc),
            detail=None if self._healthy else "Scripted unhealthy for this test.",
        )


__all__ = ["FakeRuntimeAdapter", "RuntimeAdapterError"]
