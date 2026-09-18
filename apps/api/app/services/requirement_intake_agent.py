"""Phase 16: Requirement Intake — the first of six agents this migration
names for individual replacement (recommended order: Requirement Intake,
Problem Discovery, Story Crafting, Implementation Planning, Solution
Discovery, HLD, Story LLD). Only Requirement Intake is migrated in this
pass — see docs/architecture/agent-migration-requirement-intake.md for
why the other six are deferred rather than rushed through the same
recipe without individual verification.

Classification: LANGGRAPH_BOUNDED_LOOP. Requirement Intake already goes
through generate -> validate -> repair -> clarify -> resume via
LoopEngineService (Phase 00 baseline section 1.1) — the exact five
behaviors this phase says LangGraph should provide. A real `langgraph`
package dependency was NOT introduced: this environment has no verified
network access to install and audit a new third-party dependency (the
same disclosed constraint as Phase 12's vendor-SDK blocker). This module
reimplements only the instruction-construction and validation-ordering
layer using the existing generation/provider machinery, not a
replacement loop engine.

What's different from the legacy path (app.services.ai_generation.generate,
still the path every real run uses by default today):
  1. Instructions come from PromptCompiler.compile() (skill
     "requirement-analysis"), not AgentPrompt.system_prompt +
     build_prioritized_context.
  2. A deterministic pre-check runs BEFORE any LLM call: are the three
     required Requirement Intake fields (business_objective, users,
     current_problem — the exact required fields
     apps/web/lib/structured-actions/schemas.ts's Phase 15 form already
     enforces client-side) present at all? Missing fields short-circuit
     straight to CLARIFICATION_REQUIRED, naming exactly which ones, with
     zero LLM calls spent.
  3. A deterministic post-check runs on the drafted content (non-trivial
     length) before accepting it. On failure, exactly ONE repair call is
     made (regenerate with feedback) — "allow one repair call by
     default" — and its result is accepted either way; this is
     intentionally bounded to one repair, never a further loop.
  4. Output is the canonical ExecutionResult (app.agent_runtime), in
     addition to the existing AgentGenerationResult-shaped fields a
     caller would need to persist the artifact exactly like the legacy
     path does — preserving the existing Artifact/ArtifactVersion/review
     gate/graph-transition mechanics, which this module does not touch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.agent_runtime import (
    ExecutionResult,
    RuntimeCapability,
    RuntimeCapabilityManifest,
    UsageEvidence,
    WorkObjective,
    WorkPacket,
    WorkPacketTaskType,
)
from app.agent_runtime.enums import ExecutionState
from app.agent_runtime.execution_result import ClarificationRequest
from app.models import Project
from app.models.enums import DataClassification, ProjectExecutionProfileSource, ProjectExecutionProfileStatus, ProjectExecutionProfileType
from app.prompt_compiler.compiler import PromptCompiler
from app.schemas.project_execution_profile import NetworkPolicy, ProjectExecutionProfileRead
from app.services import ai_generation
from app.services.ai_generation import AgentGenerationResult

REQUIRED_FIELDS = ("business_objective", "users", "current_problem")
MIN_DRAFT_LENGTH = 40  # deterministic post-check floor — "not a near-empty draft," not a quality judgement.


class RequirementIntakeAgentError(Exception):
    pass


def _document_profile_placeholder(project: Project) -> ProjectExecutionProfileRead:
    """PromptCompiler.compile() requires a full ProjectExecutionProfileRead
    even for a pure document task_type that never touches a repository —
    REQUIREMENT_ANALYSIS has no execution-profile concept of its own. This
    constructs a synthetic, never-persisted profile purely to satisfy that
    parameter; every DB-identity field is a placeholder, never written or
    read back. Same construction shape this codebase's own
    test_prompt_compiler_compiler.py already uses for a
    not-backed-by-a-real-row profile, so this is a consistent use of an
    already-tolerated pattern, not a new one invented here."""
    now = datetime.now(timezone.utc)
    return ProjectExecutionProfileRead(
        id=uuid.uuid4(), project_id=project.id, version=0, status=ProjectExecutionProfileStatus.APPROVED, is_active=True,
        profile_type=ProjectExecutionProfileType.NEW_PROJECT, source=ProjectExecutionProfileSource.TEMPLATE,
        template_key=None, repository_id=None, repository_snapshot_id=None, detection_metadata={},
        created_by_id=project.created_by_id, submitted_at=None, approved_by_id=None, approved_at=None,
        rejected_by_id=None, rejected_at=None, rejection_reason=None, created_at=now, updated_at=now,
        network_policy=NetworkPolicy(), data_classification=DataClassification.INTERNAL,
    )


def _document_capability() -> RuntimeCapabilityManifest:
    return RuntimeCapabilityManifest(
        runtime_name="requirement-intake-v2", max_context_tokens=8000, max_output_tokens=2048,
        supported_tool_categories=[], supports_structured_output=False,
        response_formats=["markdown_with_clarification_header"],
        capabilities=[RuntimeCapability.HUMAN_APPROVAL, RuntimeCapability.USAGE_REPORTING],
    )


def missing_required_fields(freeform_context: dict[str, str]) -> list[str]:
    """The deterministic pre-check — "deterministic validation before LLM
    validation," run before any WorkPacket is even built. Returns the
    exact field keys missing, so a clarification response can name them."""
    return [key for key in REQUIRED_FIELDS if not (freeform_context.get(key) or "").strip()]


def build_work_packet(project: Project, freeform_context: dict[str, str], created_by_user_id: uuid.UUID | None) -> WorkPacket:
    goal = freeform_context.get("business_objective", "").strip()
    success_definition = freeform_context.get("success_measures", "").strip() or "Requirement intake is approved by a human reviewer."
    return WorkPacket(
        packet_id=uuid.uuid4(), task_type=WorkPacketTaskType.REQUIREMENT_ANALYSIS, project_id=project.id,
        objective=WorkObjective(goal=goal or "Draft a requirement intake document.", success_definition=success_definition),
        created_at=datetime.now(timezone.utc), created_by_user_id=created_by_user_id,
    )


@dataclass
class RequirementIntakeV2Outcome:
    """What a caller needs both to persist an artifact exactly like the
    legacy path does, and to record shadow-comparison metrics (see
    app/services/agent_migration_shadow.py)."""

    execution_result: ExecutionResult
    content_markdown: str
    needs_clarification: bool
    clarification_questions: list[str]
    repair_attempted: bool


def _deterministic_content_ok(content_markdown: str) -> bool:
    return len(content_markdown.strip()) >= MIN_DRAFT_LENGTH


def _compile_instructions(project: Project, packet: WorkPacket, extra_context: str = "") -> str:
    compiled = PromptCompiler().compile(work_packet=packet, capability=_document_capability(), profile=_document_profile_placeholder(project))
    parts = [compiled.short_system_instruction, compiled.task_instruction]
    if extra_context:
        parts.append(extra_context)
    parts.append(ai_generation.response_format_instructions())
    return "\n\n".join(parts)


def _dispatch(system_prompt: str, user_content: str, output_token_budget: int) -> AgentGenerationResult:
    """Mirrors ai_generation.generate()'s own post-context-assembly
    provider dispatch (same functions, same behavior) — the only thing
    this module builds differently is what goes INTO system_prompt/
    user_content (PromptCompiler output, not build_prioritized_context
    output). Reuses the real, already-tested provider-calling functions
    rather than reimplementing any HTTP/SDK call."""
    provider = ai_generation.get_active_provider()
    if provider == "mock":
        from app.services import mock_agent

        output_text = f"# Requirement Intake (v2 skill-compiled draft)\n\n{user_content[:600]}"
        token_usage, cost = mock_agent.estimate_mock_usage(prompt_text=system_prompt, output_text=output_text)
        return AgentGenerationResult(
            content_markdown=output_text, needs_clarification=False,
            prompt_tokens=token_usage["prompt_tokens"], completion_tokens=token_usage["completion_tokens"],
            total_tokens=token_usage["total_tokens"], cost=cost, used_mock=True,
        )
    if provider == "gemini":
        return ai_generation._generate_with_gemini(system_prompt, user_content, output_token_budget)
    if provider == "openrouter":
        return ai_generation._generate_with_openrouter(system_prompt, user_content, output_token_budget)
    if provider == "nvidia":
        return ai_generation._generate_with_nvidia(system_prompt, user_content, output_token_budget)
    if provider == "ollama":
        return ai_generation._generate_with_ollama(system_prompt, user_content, output_token_budget)
    return ai_generation._generate_with_anthropic(system_prompt, user_content, output_token_budget)


def run_requirement_intake_agent_v2(
    project: Project, freeform_context: dict[str, str], created_by_user_id: uuid.UUID | None,
    output_token_budget: int = 2048,
) -> RequirementIntakeV2Outcome:
    started_at = datetime.now(timezone.utc)
    packet = build_work_packet(project, freeform_context, created_by_user_id)

    missing = missing_required_fields(freeform_context)
    if missing:
        completed_at = datetime.now(timezone.utc)
        questions = [f"What is the {key.replace('_', ' ')}?" for key in missing]
        return RequirementIntakeV2Outcome(
            execution_result=ExecutionResult(
                result_id=uuid.uuid4(), packet_id=packet.packet_id, state=ExecutionState.CLARIFICATION_REQUIRED,
                summary=f"Missing required field(s): {', '.join(missing)}.",
                clarification=ClarificationRequest(questions=questions, blocking=True, context="Deterministic pre-check — no LLM call was made."),
                usage=UsageEvidence(), started_at=started_at, completed_at=completed_at,
            ),
            content_markdown="", needs_clarification=True, clarification_questions=questions, repair_attempted=False,
        )

    system_prompt = _compile_instructions(project, packet)
    result = _dispatch(system_prompt, packet.objective.goal, output_token_budget)
    repair_attempted = False

    if not result.needs_clarification and not _deterministic_content_ok(result.content_markdown):
        repair_attempted = True
        repair_instructions = _compile_instructions(
            project, packet,
            extra_context=f"Your previous draft was too short or empty:\n\n{result.content_markdown}\n\nProvide a complete draft this time.",
        )
        result = _dispatch(repair_instructions, packet.objective.goal, output_token_budget)

    completed_at = datetime.now(timezone.utc)
    usage = UsageEvidence(
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
        cost_usd=result.cost if result.cost is not None else 0.0, llm_calls_made=2 if repair_attempted else 1,
        wall_clock_seconds=(completed_at - started_at).total_seconds(), repair_attempts_used=1 if repair_attempted else 0,
    )

    if result.needs_clarification:
        return RequirementIntakeV2Outcome(
            execution_result=ExecutionResult(
                result_id=uuid.uuid4(), packet_id=packet.packet_id, state=ExecutionState.CLARIFICATION_REQUIRED,
                summary="The model requested clarification before drafting.",
                clarification=ClarificationRequest(questions=result.clarification_questions or ["(no specific questions were returned)"], blocking=True),
                usage=usage, started_at=started_at, completed_at=completed_at,
            ),
            content_markdown="", needs_clarification=True, clarification_questions=result.clarification_questions,
            repair_attempted=repair_attempted,
        )

    return RequirementIntakeV2Outcome(
        execution_result=ExecutionResult(
            result_id=uuid.uuid4(), packet_id=packet.packet_id, state=ExecutionState.COMPLETED,
            summary=result.content_markdown[:280], usage=usage, started_at=started_at, completed_at=completed_at,
        ),
        content_markdown=result.content_markdown, needs_clarification=False, clarification_questions=[],
        repair_attempted=repair_attempted,
    )
