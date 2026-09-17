"""Phase 02: Runtime Capability Registry — conformance tests for
FakeRuntimeAdapter, the legacy adapters, and RuntimeRegistry end to end.
See app/coding_runtime/__init__.py.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.agent_runtime.capability import RuntimeCapability
from app.coding_runtime import (
    CodingRuntimeAdapter,
    DocumentRuntimeAdapter,
    ExecutionLocation,
    FakeRuntimeAdapter,
    LegacyCodingRuntimeAdapter,
    LegacyDocumentRuntimeAdapter,
    RuntimeKind,
    RuntimeRegistry,
    RuntimeRegistryError,
    RuntimeSelectionRequest,
)
from app.coding_runtime.manifest import RuntimeConnection
from app.models import AgentDefinition, AgentPromptRole
from app.services import ai_generation, implementation_agent
from app.services.repo_context_builder import RelevantFile, RepoContextPreviewResult
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(implementation_agent, "get_active_provider", lambda: "mock")


def _work_packet(task_type=WorkPacketTaskType.HLD) -> WorkPacket:
    return WorkPacket(
        packet_id=uuid.uuid4(), task_type=task_type, project_id=uuid.uuid4(),
        objective=WorkObjective(goal="Draft the artifact.", success_definition="A complete draft exists."),
        created_at=datetime.now(timezone.utc),
    )


# --- FakeRuntimeAdapter conformance ---------------------------------------------------------


def test_fake_adapter_satisfies_both_interfaces():
    fake = FakeRuntimeAdapter()
    assert isinstance(fake, CodingRuntimeAdapter)
    assert isinstance(fake, DocumentRuntimeAdapter)


def test_fake_adapter_records_every_call_and_returns_a_completed_result():
    fake = FakeRuntimeAdapter(name="fake-1")
    packet = _work_packet()

    result = fake.execute(packet)

    assert fake.calls == [packet]
    assert result.state.value == "COMPLETED"
    assert result.packet_id == packet.packet_id


def test_fake_adapter_can_be_scripted_to_raise():
    fake = FakeRuntimeAdapter(raise_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        fake.execute(_work_packet())


def test_fake_adapter_health_reflects_the_scripted_value():
    healthy = FakeRuntimeAdapter(healthy=True)
    unhealthy = FakeRuntimeAdapter(name="down", healthy=False)

    assert healthy.check_health().healthy is True
    assert unhealthy.check_health().healthy is False
    assert unhealthy.check_health().detail is not None


# --- RuntimeRegistry --------------------------------------------------------------------------


def test_registry_rejects_duplicate_registration():
    registry = RuntimeRegistry()
    registry.register(FakeRuntimeAdapter(name="dup"))
    with pytest.raises(RuntimeRegistryError):
        registry.register(FakeRuntimeAdapter(name="dup"))


def test_registry_get_raises_for_an_unregistered_name():
    registry = RuntimeRegistry()
    with pytest.raises(RuntimeRegistryError):
        registry.get("nope")


def test_registry_select_skips_an_unavailable_runtime():
    registry = RuntimeRegistry()
    registry.register(
        FakeRuntimeAdapter(name="disabled", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION]),
        available=False, reason="Disabled by operator.",
    )
    registry.register(FakeRuntimeAdapter(name="enabled", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION]))

    decision = registry.select(
        RuntimeSelectionRequest(packet=_work_packet(WorkPacketTaskType.IMPLEMENT_STORY), task_type=WorkPacketTaskType.IMPLEMENT_STORY, required_capabilities=[]),
    )

    assert decision.selected_runtime == "enabled"
    assert decision.rejected["disabled"] == "Disabled by operator."


def test_registry_select_honors_actor_connection_when_given():
    registry = RuntimeRegistry()
    registry.register(FakeRuntimeAdapter(name="needs-connection", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION]))

    decision = registry.select(
        RuntimeSelectionRequest(packet=_work_packet(WorkPacketTaskType.IMPLEMENT_STORY), task_type=WorkPacketTaskType.IMPLEMENT_STORY, required_capabilities=[]),
        connections={},  # actor has no connection to anything
    )

    assert decision.selected_runtime is None
    assert "connection" in decision.rejected["needs-connection"].lower()

    connected_decision = registry.select(
        RuntimeSelectionRequest(packet=_work_packet(WorkPacketTaskType.IMPLEMENT_STORY), task_type=WorkPacketTaskType.IMPLEMENT_STORY, required_capabilities=[]),
        connections={"needs-connection": RuntimeConnection(runtime_name="needs-connection", actor_user_id="u1", connected=True)},
    )
    assert connected_decision.selected_runtime == "needs-connection"


def test_registry_select_derives_required_capabilities_from_task_type_by_default():
    """RuntimeRegistry._REQUIRED_CAPABILITIES_BY_TASK_TYPE fills in the
    capability requirement when the caller passes an empty list — a
    caller building a RuntimeSelectionRequest for IMPLEMENT_STORY
    shouldn't have to know the exact capability set by hand."""
    registry = RuntimeRegistry()
    registry.register(FakeRuntimeAdapter(name="under-capable", capabilities=[RuntimeCapability.REPOSITORY_READ]))
    registry.register(FakeRuntimeAdapter(name="capable", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION]))

    decision = registry.select(
        RuntimeSelectionRequest(packet=_work_packet(WorkPacketTaskType.IMPLEMENT_STORY), task_type=WorkPacketTaskType.IMPLEMENT_STORY, required_capabilities=[]),
    )

    assert decision.selected_runtime == "capable"
    assert "under-capable" in decision.rejected


def test_registry_check_health_delegates_to_the_adapter():
    registry = RuntimeRegistry()
    registry.register(FakeRuntimeAdapter(name="probed", healthy=False))
    assert registry.check_health("probed").healthy is False


# --- LegacyDocumentRuntimeAdapter — wraps the real ai_generation.generate() path -------------


def _document_context(db, project, actor):
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document", requires_human_approval=True)
    agent = AgentDefinition(agent_key="hld-agent", name="HLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    prompt = make_agent_prompt(db, stage="hld", role=AgentPromptRole.DRAFT, agent=agent)
    return node, prompt


def test_legacy_document_adapter_wraps_the_real_generate_call(db, project, actor):
    node, prompt = _document_context(db, project, actor)
    adapter = LegacyDocumentRuntimeAdapter(
        project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={},
        context_token_budget=4000, output_token_budget=1000,
    )
    assert adapter.definition.kind == RuntimeKind.DOCUMENT
    assert adapter.definition.execution_location == ExecutionLocation.COMPANY_SANDBOX

    packet = _work_packet(WorkPacketTaskType.HLD)
    result = adapter.execute(packet)

    assert result.packet_id == packet.packet_id
    assert result.state.value in ("COMPLETED", "CLARIFICATION_REQUIRED")
    assert result.usage.llm_calls_made == 1
    # The mock provider always produces real, deterministic output — never
    # a silent empty result — see app.services.mock_agent.
    if result.state.value == "COMPLETED":
        assert result.extensions["legacy"]["content_markdown"]


def test_legacy_document_adapter_health_reports_mock_when_no_provider_configured(db, project, actor):
    node, prompt = _document_context(db, project, actor)
    adapter = LegacyDocumentRuntimeAdapter(
        project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={},
        context_token_budget=4000, output_token_budget=1000,
    )
    health = adapter.check_health()
    assert health.healthy is True
    assert "mock" in (health.detail or "").lower()


# --- LegacyCodingRuntimeAdapter — wraps the real run_implementation_agent() path -------------


def _repo_context() -> RepoContextPreviewResult:
    return RepoContextPreviewResult(
        relevant_folders=["apps/api/app/api/routes"],
        relevant_files=[
            RelevantFile(
                path="apps/api/app/api/routes/auth.py", entry_type="FILE", size=500, score=10,
                reasons=["matches an expected path"], content_mode="full", snippet="def existing(): ...",
            ),
        ],
        architecture_summary="Touches apps/api/app/api/routes.",
        dependency_notes=[],
        suggested_edit_scope=[{"path": "apps/api/app/api/routes/auth.py", "status": "existing"}],
        token_budget_report={},
    )


class _FakeImplementationTask:
    """implementation_agent.py only reads attributes off the task it's
    given — same stand-in convention tests/test_implementation_agent.py
    already established for exactly this reason."""

    def __init__(self, **overrides):
        from app.models import ImplementationTaskArea, ImplementationTaskRiskLevel

        defaults = dict(
            title="Add password reset endpoint", description="Add a POST /auth/password-reset endpoint.",
            area=ImplementationTaskArea.BACKEND, expected_paths=["apps/api/app/api/routes/auth.py"],
            acceptance_criteria=["Returns 202 for a valid email"], test_expectation="",
            risk_level=ImplementationTaskRiskLevel.MEDIUM, linked_story="Password Reset Request",
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(self, k, v)


def test_legacy_coding_adapter_wraps_the_real_implementation_agent_call():
    adapter = LegacyCodingRuntimeAdapter(task=_FakeImplementationTask(), repo_context=_repo_context(), story=None, lld_summary="## LLD\nSome design.")
    assert adapter.definition.kind == RuntimeKind.CODING
    assert RuntimeCapability.SHELL_EXECUTION not in adapter.definition.capability.capabilities  # honesty: no real execution

    packet = _work_packet(WorkPacketTaskType.IMPLEMENT_STORY)
    result = adapter.execute(packet)

    assert result.packet_id == packet.packet_id
    assert result.state.value == "WAITING_APPROVAL"  # a human reviews it, exactly like today
    assert len(result.file_changes) >= 1
    assert result.extensions["legacy"]["diff_text"]


def test_legacy_coding_adapter_never_claims_real_execution():
    """Rule 9 (Common Instruction) — never report simulated execution as
    real. implementation_agent.py has no test-execution sandbox (Phase 00
    baseline section 8); its ExecutionResult must never carry
    CommandEvidence/TestEvidence marked real_execution=True."""
    adapter = LegacyCodingRuntimeAdapter(task=_FakeImplementationTask(), repo_context=_repo_context(), story=None, lld_summary="## LLD\n")
    result = adapter.execute(_work_packet(WorkPacketTaskType.IMPLEMENT_STORY))
    assert all(c.real_execution is False for c in result.commands)
    assert all(t.real_execution is False for t in result.test_evidence)
