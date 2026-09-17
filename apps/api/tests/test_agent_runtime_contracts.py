"""Unit tests for the Work Packet / Result contracts — see
app/agent_runtime/__init__.py's module docstring for the five hard rules
these tests enforce. Pure contract/validation tests: no DB session, no
provider, no external runtime (matches this phase's "do not connect an
external runtime yet" instruction).
"""

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.agent_runtime import (
    ApprovalPolicy,
    BudgetPolicy,
    ExecutionResult,
    ExecutionState,
    RepositoryReference,
    RuntimeInstructionPackage,
    ScopePolicy,
    WorkObjective,
    WorkPacket,
    WorkPacketTaskType,
)
from app.agent_runtime.registry import CONTRACT_REGISTRY, TOP_LEVEL_CONTRACTS

# A field name containing any of these as a whole underscore-delimited
# word is treated as "secret-shaped" for hard rule 4's enforcement below.
# Whole-word, not substring, matching is deliberate: this codebase has
# legitimate fields like `prompt_tokens`/`max_context_tokens` (a LLM TOKEN
# COUNT, not an auth TOKEN) that would false-positive on a plain substring
# check — "token" (singular) is secret-shaped, "tokens" (plural, a count)
# is not.
_SECRET_SHAPED_WORDS = {"token", "secret", "password", "credential", "credentials", "key", "apikey", "authorization"}


def _field_words(field_name: str) -> set[str]:
    return set(field_name.lower().split("_"))

ALL_TASK_TYPES = [
    "REQUIREMENT_ANALYSIS", "PROBLEM_DISCOVERY", "SOLUTION_DISCOVERY", "HLD",
    "STORY_CRAFTING_VERTICAL", "STORY_CRAFTING_HORIZONTAL", "STORY_LLD",
    "IMPLEMENTATION_PLAN", "IMPLEMENT_STORY", "TEST_STORY", "PR_REVIEW",
    "INFRASTRUCTURE_CHANGE", "MAINTENANCE_ANALYSIS",
]

ALL_EXECUTION_STATES = [
    "QUEUED", "RUNNING", "CLARIFICATION_REQUIRED", "WAITING_APPROVAL",
    "COMPLETED", "FAILED", "CANCELLED", "STALE",
]


def _minimal_work_packet(**overrides) -> WorkPacket:
    defaults = dict(
        packet_id=uuid.uuid4(),
        task_type=WorkPacketTaskType.HLD,
        project_id=uuid.uuid4(),
        objective=WorkObjective(goal="Draft the HLD.", success_definition="A complete HLD document exists."),
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return WorkPacket(**defaults)


# --- Phase 01 instruction: WorkPacket must support exactly these task types ---


def test_work_packet_task_type_covers_every_required_value():
    assert {t.value for t in WorkPacketTaskType} == set(ALL_TASK_TYPES)


@pytest.mark.parametrize("task_type", ALL_TASK_TYPES)
def test_work_packet_accepts_every_required_task_type(task_type):
    packet = _minimal_work_packet(task_type=task_type)
    assert packet.task_type.value == task_type


def test_work_packet_rejects_an_unknown_task_type():
    with pytest.raises(ValidationError):
        _minimal_work_packet(task_type="NOT_A_REAL_TASK_TYPE")


# --- Phase 01 instruction: ExecutionResult must support exactly these states ---


def test_execution_state_covers_every_required_value():
    assert {s.value for s in ExecutionState} == set(ALL_EXECUTION_STATES)


@pytest.mark.parametrize("state", ALL_EXECUTION_STATES)
def test_execution_result_accepts_every_required_state(state):
    result = ExecutionResult(result_id=uuid.uuid4(), packet_id=uuid.uuid4(), state=state)
    assert result.state.value == state


# --- Hard rule 1/2: no vendor-specific mandatory field; extra="forbid" everywhere ---


@pytest.mark.parametrize("name,model", CONTRACT_REGISTRY.items(), ids=list(CONTRACT_REGISTRY.keys()))
def test_every_contract_forbids_undeclared_fields(name, model):
    """extra='forbid' (via AgentRuntimeModel) is what makes hard rule 2
    structural — assert every registered contract actually inherits it,
    not just the ones this test happens to construct elsewhere."""
    assert model.model_config.get("extra") == "forbid", f"{name} must set extra='forbid'"


def test_work_packet_rejects_an_undeclared_top_level_field():
    with pytest.raises(ValidationError):
        WorkPacket.model_validate(
            {
                "packet_id": str(uuid.uuid4()),
                "task_type": "HLD",
                "project_id": str(uuid.uuid4()),
                "objective": {"goal": "x", "success_definition": "y"},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "anthropic_model": "claude-opus-5",  # exactly the kind of vendor-mandatory field hard rule 1 forbids
            }
        )


def test_vendor_metadata_only_fits_through_the_namespaced_extensions_field():
    packet = _minimal_work_packet(extensions={"anthropic": {"preferred_model": "claude-opus-5"}, "internal": {"note": "x"}})
    assert packet.extensions["anthropic"]["preferred_model"] == "claude-opus-5"
    assert packet.extensions["internal"]["note"] == "x"
    # No contract field is itself named after a vendor — see test below for
    # the general form of this assertion across the whole registry.


@pytest.mark.parametrize("name,model", CONTRACT_REGISTRY.items(), ids=list(CONTRACT_REGISTRY.keys()))
def test_no_contract_field_is_vendor_named(name, model):
    known_vendor_words = ("anthropic", "openai", "gemini", "google", "openrouter", "nvidia", "ollama", "langgraph", "claude", "gpt")
    for field_name in model.model_fields:
        lowered = field_name.lower()
        assert not any(v in lowered for v in known_vendor_words), f"{name}.{field_name} looks vendor-specific"


# --- Hard rule 3: references, not content ---


def test_artifact_reference_has_no_full_content_field():
    from app.agent_runtime import ArtifactReference

    assert "content" not in ArtifactReference.model_fields
    assert "full_content" not in ArtifactReference.model_fields
    assert "content_markdown" not in ArtifactReference.model_fields
    assert "summary" in ArtifactReference.model_fields  # the one, bounded, bookkeeping exception


# --- Hard rule 4: never a secret-shaped field ---


@pytest.mark.parametrize("name,model", CONTRACT_REGISTRY.items(), ids=list(CONTRACT_REGISTRY.keys()))
def test_no_contract_field_is_secret_shaped(name, model):
    for field_name in model.model_fields:
        assert not (_field_words(field_name) & _SECRET_SHAPED_WORDS), f"{name}.{field_name} looks secret-shaped"


# --- RepositoryReference: base branch + immutable base commit SHA ---


def test_repository_reference_requires_base_branch_and_commit_sha():
    with pytest.raises(ValidationError):
        RepositoryReference(provider="github", owner="o", name="n", base_branch="main")  # missing base_commit_sha

    ref = RepositoryReference(provider="github", owner="o", name="n", base_branch="main", base_commit_sha="0123456789abcdef")
    assert ref.base_branch == "main"
    assert ref.base_commit_sha == "0123456789abcdef"


# --- ScopePolicy: allowed and denied file paths ---


def test_scope_policy_carries_allowed_and_denied_paths():
    policy = ScopePolicy(allowed_paths=["apps/api/**"], denied_paths=["apps/api/.env"])
    assert policy.allowed_paths == ["apps/api/**"]
    assert policy.denied_paths == ["apps/api/.env"]
    assert policy.deny_by_default is True  # fail-closed default


# --- BudgetPolicy: max cost, time, calls, tools, repair attempts ---


def test_budget_policy_carries_every_required_ceiling():
    policy = BudgetPolicy(
        max_cost_usd=1.5, max_wall_clock_seconds=300, max_llm_calls=5,
        max_tool_calls=20, max_distinct_tools=4, max_repair_attempts=3,
    )
    assert policy.max_cost_usd == 1.5
    assert policy.max_wall_clock_seconds == 300
    assert policy.max_llm_calls == 5
    assert policy.max_tool_calls == 20
    assert policy.max_distinct_tools == 4
    assert policy.max_repair_attempts == 3


def test_budget_policy_ceilings_are_all_optional():
    policy = BudgetPolicy()
    assert policy.max_cost_usd is None
    assert policy.max_repair_attempts is None


def test_budget_policy_rejects_negative_ceilings():
    with pytest.raises(ValidationError):
        BudgetPolicy(max_cost_usd=-1.0)
    with pytest.raises(ValidationError):
        BudgetPolicy(max_repair_attempts=-1)


# --- ApprovalPolicy internal consistency ---


def test_approval_policy_rejects_named_approvers_without_requiring_approval():
    with pytest.raises(ValidationError):
        ApprovalPolicy(requires_human_approval=False, approval_roles=["TECH_LEAD"])


def test_approval_policy_allows_no_approvers_named_when_approval_not_required():
    policy = ApprovalPolicy(requires_human_approval=False)
    assert policy.approval_roles == []


# --- WorkPacket: repository optional, upstream artifacts/knowledge are references ---


def test_work_packet_repository_is_optional_for_a_non_code_task():
    packet = _minimal_work_packet(task_type=WorkPacketTaskType.HLD)
    assert packet.repository is None


def test_work_packet_default_policies_are_present_even_when_not_specified():
    packet = _minimal_work_packet()
    assert isinstance(packet.scope_policy, ScopePolicy)
    assert isinstance(packet.budget_policy, BudgetPolicy)
    assert isinstance(packet.approval_policy, ApprovalPolicy)


# --- ExecutionResult: failure/clarification shape ---


def test_execution_result_failed_state_can_carry_a_failure():
    from app.agent_runtime import RuntimeFailure, FailureCategory

    result = ExecutionResult(
        result_id=uuid.uuid4(), packet_id=uuid.uuid4(), state=ExecutionState.FAILED,
        failure=RuntimeFailure(category=FailureCategory.PROVIDER_ERROR, message="upstream provider returned a non-2xx response", retryable=True),
    )
    assert result.failure.category == FailureCategory.PROVIDER_ERROR
    assert result.failure.retryable is True


def test_execution_result_clarification_requires_at_least_one_question():
    from app.agent_runtime import ClarificationRequest

    with pytest.raises(ValidationError):
        ClarificationRequest(questions=[])


# --- Honesty rule carried forward from Phase 00: real_execution is required, not defaulted ---


def test_command_evidence_requires_explicit_real_execution_flag():
    from app.agent_runtime import CommandEvidence

    with pytest.raises(ValidationError):
        CommandEvidence.model_validate({"command": "pytest -q"})  # real_execution omitted — must be explicit, never defaulted to a "safe" guess

    evidence = CommandEvidence(command="pytest -q", real_execution=False)
    assert evidence.real_execution is False


def test_test_evidence_requires_explicit_real_execution_flag():
    from app.agent_runtime import TestEvidence, TestResult

    with pytest.raises(ValidationError):
        TestEvidence.model_validate({"name": "test_x", "result": "PASS"})  # real_execution omitted

    evidence = TestEvidence(name="test_x", result=TestResult.PASS_, real_execution=False)
    assert evidence.real_execution is False


# --- RuntimeInstructionPackage wraps a WorkPacket without duplicating it ---


def test_runtime_instruction_package_wraps_one_work_packet():
    packet = _minimal_work_packet()
    package = RuntimeInstructionPackage(
        packet=packet,
        system_instructions="Draft the HLD per the project's standard format.",
        response_contract_description="Markdown with a two-part clarification header.",
        generated_at=datetime.now(timezone.utc),
    )
    assert package.packet.packet_id == packet.packet_id
    assert package.schema_version == "1.0.0"


# --- Registry completeness: every contract this phase names is actually registered ---


def test_registry_contains_every_contract_named_in_this_phases_instructions():
    required_names = {
        "WorkPacket", "WorkObjective", "RepositoryReference", "ArtifactReference",
        "KnowledgeReference", "AcceptanceCriterion", "ScopePolicy", "ToolPolicy",
        "RequiredCheck", "BudgetPolicy", "ApprovalPolicy", "RuntimeInstructionPackage",
        "ExecutionResult", "FileChangeEvidence", "CommandEvidence", "TestEvidence",
        "UsageEvidence", "RuntimeFailure", "ClarificationRequest",
    }
    assert required_names <= set(CONTRACT_REGISTRY.keys())


def test_top_level_contracts_all_carry_a_pinned_schema_version_field():
    for name in TOP_LEVEL_CONTRACTS:
        model = CONTRACT_REGISTRY[name]
        assert "schema_version" in model.model_fields, f"{name} must carry schema_version"
