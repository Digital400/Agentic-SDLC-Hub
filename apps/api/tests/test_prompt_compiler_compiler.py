"""Tests for PromptCompiler — see app/prompt_compiler/compiler.py.

Covers: end-to-end compile(), the RuntimeInstructionPackage conversion,
instruction deduplication, priority-based token trimming, static-prefix
caching, and hash stability/sensitivity.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.agent_runtime import (
    AcceptanceCriterion,
    ArtifactReference,
    KnowledgeReference,
    RuntimeCapabilityManifest,
    WorkObjective,
    WorkPacket,
    WorkPacketTaskType,
)
from app.agent_runtime.policies import BudgetPolicy
from app.models.enums import DataClassification, ProjectExecutionProfileSource, ProjectExecutionProfileStatus, ProjectExecutionProfileType
from app.prompt_compiler.compiler import PromptCompiler, dedupe_instructions, _STATIC_PREFIX_CACHE
from app.prompt_compiler.lint import PromptLintError
from app.schemas.project_execution_profile import NetworkPolicy, ProjectExecutionProfileRead


def _capability(**overrides) -> RuntimeCapabilityManifest:
    defaults = dict(
        runtime_name="test-runtime", max_context_tokens=8000, max_output_tokens=2048,
        supported_tool_categories=["file_read", "file_write", "shell_command"],
        supports_structured_output=True, response_formats=["structured_json", "markdown_with_clarification_header"],
    )
    defaults.update(overrides)
    return RuntimeCapabilityManifest(**defaults)


def _profile(**overrides) -> ProjectExecutionProfileRead:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(), project_id=uuid.uuid4(), version=1, status=ProjectExecutionProfileStatus.APPROVED, is_active=True,
        profile_type=ProjectExecutionProfileType.EXISTING_REPOSITORY, source=ProjectExecutionProfileSource.DETECTED,
        template_key=None, repository_id=None, repository_snapshot_id=None, detection_metadata={},
        created_by_id=uuid.uuid4(), submitted_at=None, approved_by_id=None, approved_at=None, rejected_by_id=None,
        rejected_at=None, rejection_reason=None, created_at=now, updated_at=now,
        working_directories=["apps/api"], unit_test_command="pytest -q", lint_command="ruff check .",
        allowed_paths=["apps/api/**"], denied_paths=[".env"], allowed_command_patterns=["pytest -q"],
        denied_command_patterns=["rm -rf *"], network_policy=NetworkPolicy(), data_classification=DataClassification.INTERNAL,
    )
    defaults.update(overrides)
    return ProjectExecutionProfileRead(**defaults)


def _work_packet(**overrides) -> WorkPacket:
    defaults = dict(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.IMPLEMENT_STORY, project_id=uuid.uuid4(),
        objective=WorkObjective(goal="Implement the password reset endpoint.", success_definition="Endpoint exists and tests pass."),
        acceptance_criteria=[AcceptanceCriterion(id="AC-1", description="Link expires after 1 hour.")],
        upstream_artifacts=[ArtifactReference(artifact_id=uuid.uuid4(), artifact_type="story_lld", status="APPROVED", summary="LLD summary text.")],
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return WorkPacket(**defaults)


@pytest.fixture(autouse=True)
def _clear_static_prefix_cache():
    _STATIC_PREFIX_CACHE.clear()
    yield
    _STATIC_PREFIX_CACHE.clear()


# --- End-to-end compile ------------------------------------------------------------------


def test_compile_produces_every_required_output_field():
    result = PromptCompiler().compile(work_packet=_work_packet(), capability=_capability(), profile=_profile())

    assert result.short_system_instruction
    assert result.task_instruction
    assert isinstance(result.context_references, list)
    assert result.policy_files
    assert result.output_schema["format"] == "structured_json"
    assert result.runtime_configuration["response_format"] == "structured_json"
    assert result.token_allocation_report
    assert len(result.compiler_hash) == 64  # sha256 hex digest
    assert len(result.static_prefix_hash) == 64
    assert result.skill_key == "implement-story"


def test_compile_selects_markdown_contract_for_non_structured_task_types():
    result = PromptCompiler().compile(
        work_packet=_work_packet(task_type=WorkPacketTaskType.HLD, upstream_artifacts=[]),
        capability=_capability(), profile=_profile(),
    )
    assert result.output_schema["format"] == "markdown_with_clarification_header"


def test_compile_to_runtime_instruction_package():
    packet = _work_packet()
    result = PromptCompiler().compile(work_packet=packet, capability=_capability(), profile=_profile())
    rip = result.to_runtime_instruction_package(packet=packet)

    assert rip.packet.packet_id == packet.packet_id
    assert rip.schema_version == "1.0.0"
    assert result.short_system_instruction in rip.system_instructions
    assert rip.generator_metadata["compiler_hash"] == result.compiler_hash
    assert rip.generator_metadata["skill_key"] == "implement-story"


# --- Hash stability / sensitivity ---------------------------------------------------------


def test_compiler_hash_is_stable_for_identical_inputs():
    packet_id = uuid.uuid4()
    project_id = uuid.uuid4()
    fixed_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    packet_kwargs = dict(packet_id=packet_id, project_id=project_id, created_at=fixed_time, upstream_artifacts=[])

    r1 = PromptCompiler().compile(work_packet=_work_packet(**packet_kwargs), capability=_capability(), profile=_profile())
    r2 = PromptCompiler().compile(work_packet=_work_packet(**packet_kwargs), capability=_capability(), profile=_profile())

    assert r1.compiler_hash == r2.compiler_hash
    assert r1.static_prefix_hash == r2.static_prefix_hash


def test_compiler_hash_changes_when_task_type_changes():
    r1 = PromptCompiler().compile(work_packet=_work_packet(task_type=WorkPacketTaskType.HLD, upstream_artifacts=[]), capability=_capability(), profile=_profile())
    r2 = PromptCompiler().compile(work_packet=_work_packet(task_type=WorkPacketTaskType.IMPLEMENT_STORY), capability=_capability(), profile=_profile())
    assert r1.compiler_hash != r2.compiler_hash
    assert r1.static_prefix_hash != r2.static_prefix_hash  # different skill entirely


def test_compiler_hash_changes_when_execution_profile_commands_change():
    packet = _work_packet()
    r1 = PromptCompiler().compile(work_packet=packet, capability=_capability(), profile=_profile(lint_command="ruff check ."))
    r2 = PromptCompiler().compile(work_packet=packet, capability=_capability(), profile=_profile(lint_command="eslint ."))
    assert r1.compiler_hash != r2.compiler_hash
    assert r1.static_prefix_hash == r2.static_prefix_hash  # skill/policy identity unchanged — only the profile changed


def test_static_prefix_hash_unaffected_by_work_packet_objective():
    r1 = PromptCompiler().compile(work_packet=_work_packet(objective=WorkObjective(goal="Goal A.", success_definition="Done A.")), capability=_capability(), profile=_profile())
    r2 = PromptCompiler().compile(work_packet=_work_packet(objective=WorkObjective(goal="Goal B.", success_definition="Done B.")), capability=_capability(), profile=_profile())
    assert r1.static_prefix_hash == r2.static_prefix_hash
    assert r1.compiler_hash != r2.compiler_hash


# --- Static-prefix caching ----------------------------------------------------------------


def test_static_prefix_is_cached_across_compilations_sharing_skill_and_policies():
    assert len(_STATIC_PREFIX_CACHE) == 0
    PromptCompiler().compile(work_packet=_work_packet(), capability=_capability(), profile=_profile())
    assert len(_STATIC_PREFIX_CACHE) == 1

    PromptCompiler().compile(work_packet=_work_packet(), capability=_capability(), profile=_profile())
    assert len(_STATIC_PREFIX_CACHE) == 1  # same skill+policy identity — no new cache entry

    PromptCompiler().compile(work_packet=_work_packet(task_type=WorkPacketTaskType.HLD, upstream_artifacts=[]), capability=_capability(), profile=_profile())
    assert len(_STATIC_PREFIX_CACHE) == 2  # a different skill — new entry


# --- Instruction deduplication -------------------------------------------------------------


def test_dedupe_instructions_removes_exact_and_whitespace_normalized_duplicates():
    lines = ["Never do X.", "  Never   do X.  ", "Never do Y.", "NEVER DO X."]
    assert dedupe_instructions(lines) == ["Never do X.", "Never do Y."]


def test_dedupe_instructions_preserves_order_of_first_occurrence():
    lines = ["b", "a", "b", "c", "a"]
    assert dedupe_instructions(lines) == ["b", "a", "c"]


def test_dedupe_instructions_drops_empty_lines():
    assert dedupe_instructions(["a", "", "   ", "a"]) == ["a"]


def test_compiled_denied_command_patterns_are_deduplicated():
    profile = _profile(denied_command_patterns=["rm -rf *", "rm -rf *", "sudo *"])
    result = PromptCompiler().compile(work_packet=_work_packet(), capability=_capability(), profile=profile)
    assert result.runtime_configuration["denied_command_patterns"].count("rm -rf *") == 1


# --- Priority-based token trimming (reuses TokenBudgetService) ----------------------------


def test_small_budget_drops_low_priority_rag_references_first():
    packet = _work_packet(
        budget_policy=BudgetPolicy(max_context_tokens=120),
        knowledge_context=[
            KnowledgeReference(chunk_id="c1", source_id="s1", source_title="Standard One", content_type="COMPANY_STANDARD", similarity=0.5, snippet="x " * 200),
        ],
    )
    result = PromptCompiler().compile(work_packet=packet, capability=_capability(), profile=_profile())

    assert result.token_allocation_report["context_token_budget"] == 120
    dropped_labels = [b["label"] for b in result.token_allocation_report["blocks"] if not b["included"]]
    assert any(label.startswith("rag:") for label in dropped_labels)
    # P0 (objective + skill) must never be dropped, even under a tight budget.
    included_labels = [b["label"] for b in result.token_allocation_report["blocks"] if b["included"]]
    assert "objective" in included_labels


def test_generous_budget_includes_everything():
    packet = _work_packet(
        knowledge_context=[KnowledgeReference(chunk_id="c1", source_id="s1", source_title="Standard One", content_type="COMPANY_STANDARD", similarity=0.5, snippet="A short snippet.")],
    )
    result = PromptCompiler().compile(work_packet=packet, capability=_capability(max_context_tokens=50000), profile=_profile())
    assert not result.token_allocation_report["over_budget"]
    assert all(b["included"] for b in result.token_allocation_report["blocks"])


def test_runtime_capability_ceiling_wins_over_a_larger_budget_policy_request():
    packet = _work_packet(budget_policy=BudgetPolicy(max_context_tokens=50000))
    result = PromptCompiler().compile(work_packet=packet, capability=_capability(max_context_tokens=500), profile=_profile())
    assert result.token_allocation_report["context_token_budget"] == 500


# --- Lint integration: a BLOCKING finding fails compilation --------------------------------


def test_compile_raises_prompt_lint_error_for_open_network_on_restricted_project():
    profile = _profile(data_classification=DataClassification.RESTRICTED, network_policy=NetworkPolicy(default="ALLOW"))
    with pytest.raises(PromptLintError):
        PromptCompiler().compile(work_packet=_work_packet(), capability=_capability(), profile=profile)


# --- Tool category intersection -------------------------------------------------------------


def test_runtime_configuration_only_allows_the_intersection_of_skill_and_capability_tool_categories():
    result = PromptCompiler().compile(
        work_packet=_work_packet(), capability=_capability(supported_tool_categories=["file_read", "network_request"]), profile=_profile(),
    )
    # implement-story skill allows file_read, file_write, shell_command — capability only supports file_read, network_request.
    assert result.runtime_configuration["allowed_tool_categories"] == ["file_read"]
