"""WorkPacket — the top-level "here is one unit of work" envelope — and
RuntimeInstructionPackage, the rendered form of a WorkPacket meant to be
handed to a concrete runtime. See app/agent_runtime/__init__.py.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.agent_runtime.base import CURRENT_SCHEMA_VERSION, SchemaVersion, VendorExtensible
from app.agent_runtime.common import AcceptanceCriterion, ArtifactReference, KnowledgeReference, RepositoryReference
from app.agent_runtime.enums import WorkPacketTaskType
from app.agent_runtime.policies import ApprovalPolicy, BudgetPolicy, RequiredCheck, ScopePolicy, ToolPolicy


class WorkObjective(VendorExtensible):
    """What this task is actually trying to accomplish — kept separate
    from WorkPacket itself so "the goal" and "the guardrails around
    achieving it" (scope/tool/budget/approval policies) are independently
    readable and independently testable."""

    goal: str = Field(..., min_length=1, description="A single, concrete statement of what a completed task should have achieved.")
    success_definition: str = Field(..., min_length=1, description="How a resolver/human should recognize the goal was met — distinct from acceptance_criteria, which are individually checkable sub-conditions.")
    non_goals: list[str] = Field(default_factory=list, description="Explicitly out of scope — mirrors the existing 'Out of Scope' section convention (see STORY_LLD_SECTIONS, Phase 00 baseline appendix).")


class WorkPacket(VendorExtensible):
    """One self-contained, vendor-neutral unit of work — the contract a
    future scheduler would hand to a runtime resolver. See the package
    docstring (app/agent_runtime/__init__.py) for the five hard rules every
    field below was designed to satisfy; in particular, `repository`,
    `upstream_artifacts`, and `knowledge_context` all carry REFERENCES, not
    content (hard rule 3).

    `repository` is optional because not every task_type touches a
    repository (e.g. HLD, STORY_CRAFTING_VERTICAL/HORIZONTAL, or
    MAINTENANCE_ANALYSIS when it's a documentation-only review, produce a
    document, not a code change) — a resolver for a repository-touching
    task_type (IMPLEMENT_STORY, PR_REVIEW, INFRASTRUCTURE_CHANGE, ...) is
    expected to require it be present, but that requirement is task-type
    specific and therefore a resolver-level concern, not a schema-level
    `Optional` vs. required split this contract can express generically.
    """

    schema_version: SchemaVersion = CURRENT_SCHEMA_VERSION
    packet_id: uuid.UUID = Field(..., description="Stable identity for this unit of work — referenced back by ExecutionResult.packet_id.")
    task_type: WorkPacketTaskType

    project_id: uuid.UUID
    story_id: uuid.UUID | None = Field(None, description="Set for a story-scoped task_type (STORY_LLD, IMPLEMENT_STORY, TEST_STORY, PR_REVIEW, ...); None for a project-level task_type.")

    objective: WorkObjective
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)

    repository: RepositoryReference | None = None
    upstream_artifacts: list[ArtifactReference] = Field(default_factory=list, description="Approved upstream artifacts this task depends on — by reference, never full content (hard rule 3).")
    knowledge_context: list[KnowledgeReference] = Field(default_factory=list, description="Retrieved Knowledge Base chunks relevant to this task — mirrors app/services/retrieval.py's RetrievedChunk list.")

    scope_policy: ScopePolicy = Field(default_factory=ScopePolicy)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    required_checks: list[RequiredCheck] = Field(default_factory=list)
    budget_policy: BudgetPolicy = Field(default_factory=BudgetPolicy)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)

    created_at: datetime
    created_by_user_id: uuid.UUID | None = Field(None, description="None permitted — a system-triggered packet (e.g. a scheduled maintenance scan) may have no human actor.")


class RuntimeInstructionPackage(VendorExtensible):
    """The rendered form of a WorkPacket actually meant to be handed to a
    concrete runtime — separate from WorkPacket itself so "the abstract
    unit of work" and "the specific rendering of it for execution" can
    version/change independently (a rendering strategy might change without
    the underlying packet's meaning changing at all).

    Per hard rule 5, THIS PACKAGE DOES NOT RENDER ONE OF THESE — no
    function in this package turns a WorkPacket into a
    RuntimeInstructionPackage yet. This is the target shape such a renderer
    would need to produce, defined ahead of that renderer existing, exactly
    as the strangler-migration approach requires.
    """

    schema_version: SchemaVersion = CURRENT_SCHEMA_VERSION
    packet: WorkPacket

    system_instructions: str = Field(..., min_length=1, description="The rendered system-level instructions for a runtime to follow — vendor-neutral prose, not a provider-specific prompt template.")
    response_contract_description: str = Field(
        ...,
        min_length=1,
        description="Plain-language description of the expected response shape (e.g. 'a single JSON object matching ExecutionResult' or 'Markdown with a two-part clarification header') — kept as prose, not a nested JSON Schema, so this contract itself never needs to embed another contract's schema recursively.",
    )
    expected_output_schema_ref: str | None = Field(
        None, description="Optional pointer to a JSON Schema file this response should validate against, e.g. 'ExecutionResult.schema.json' (see app/agent_runtime/json_schema/) — a reference, not an embedded schema.",
    )

    generated_at: datetime
    generator_metadata: dict[str, Any] = Field(default_factory=dict, description="Non-vendor-namespaced bookkeeping about the render itself (e.g. template version) — distinct from `extensions`, which is vendor-namespaced. Kept generic/optional; not interpreted by this package.")
