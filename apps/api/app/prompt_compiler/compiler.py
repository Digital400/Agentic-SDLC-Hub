"""PromptCompiler — combines a WorkPacket, a RuntimeCapabilityManifest, an
approved ProjectExecutionProfile, a pinned skill version, applicable
policies, and selected RAG references into one RuntimeInstructionPackage.

STATUS (Phase 04): a new, additive compilation path. Nothing in
app/services/ai_generation.py, app/services/loop_engine.py, or any
existing route calls this module — the existing AgentPrompt/DB-driven
prompt system (Phase 00 baseline sections 3, 6) is fully preserved and
keeps generating every real agent run exactly as it always has. This is
the same "define the target shape before anything calls it" strangler
posture Phase 01's app/agent_runtime package and Phase 03's
ProjectExecutionProfile both started from.

REUSE, NOT REBUILD: token trimming reuses app/services/token_budget.py's
TokenBudgetService/ContextBlock — the exact same priority-fit-or-drop
algorithm every real agent call already goes through (Phase 00 baseline
section 4), applied here to skill/policy/context content instead of
artifact/RAG content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agent_runtime import (
    KnowledgeReference,
    RuntimeCapabilityManifest,
    RuntimeInstructionPackage,
    WorkPacket,
)
from app.models.enums import DataClassification, NetworkPolicyDefault
from app.prompt_compiler.lint import LintFinding, lint_compiled_text, raise_if_blocking
from app.prompt_compiler.policy import Policy, load_applicable_policies
from app.prompt_compiler.skill import Skill, load_skill_for_task_type
from app.schemas.project_execution_profile import ProjectExecutionProfileRead
from app.services.token_budget import ContextBlock, TokenBudgetService

# Structured-JSON response contract task types — mirrors the Phase 00
# baseline's confirmed finding (section 1.4) that implementation_agent.py/
# testing_agent.py/pr_review_agent.py share one JSON response contract,
# distinct from every other stage's Markdown two-part draft/clarification
# contract (ai_generation.py, section 6.1). PromptCompiler picks the
# response_format this same way rather than inventing a third shape.
_STRUCTURED_JSON_TASK_TYPES = {"IMPLEMENT_STORY", "TEST_STORY", "PR_REVIEW"}


class PromptCompilerError(Exception):
    """A caller/input error (unknown skill version, empty capability
    manifest) — not a lint failure (see PromptLintError for that)."""


def dedupe_instructions(lines: list[str]) -> list[str]:
    """Case/whitespace-normalized de-duplication, order-preserving (first
    occurrence wins) — the "Instruction deduplication" this phase's
    instructions require. Deliberately simple (exact-after-normalization,
    not fuzzy/semantic matching): a compiled prompt combines a skill's
    own prohibited_actions with every applicable policy's rules, and the
    same guardrail stated identically in two places is a real, common
    case this catches; near-duplicate-but-differently-worded lines are
    left alone rather than risking dropping a genuinely distinct rule."""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        normalized = " ".join(line.split()).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(line)
    return result


def _stable_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _render_static_prefix(skill: Skill, policies: list[Policy]) -> str:
    """The portion of a compiled prompt that depends ONLY on which skill
    version + policy versions are selected — never on the WorkPacket, the
    ProjectExecutionProfile, or RAG references. Identical inputs always
    render identical text, which is exactly what makes this cacheable
    (see _STATIC_PREFIX_CACHE) and what makes static_prefix_hash a stable
    key a real provider-side prompt cache could be keyed on later."""
    parts = [
        f"# Skill: {skill.name} (v{skill.version})",
        f"Purpose: {skill.purpose}",
        "## Procedure",
        "\n".join(f"{i}. {step}" for i, step in enumerate(skill.procedure, start=1)),
        "## Validation Checklist",
        "\n".join(f"- {item}" for item in skill.validation_checklist),
        "## Output Contract",
        skill.output_contract,
    ]
    if skill.examples:
        parts.append("## Examples")
        for ex in skill.examples:
            parts.append(f"### {ex.scenario}\nInput: {ex.input}\nOutput: {ex.output}")

    guardrails = dedupe_instructions(list(skill.prohibited_actions) + [rule for p in policies for rule in p.rules])
    parts.append("## Guardrails (never violate any of these)")
    parts.append("\n".join(f"- {g}" for g in guardrails))

    return "\n\n".join(parts)


# Static-prefix cache — see _render_static_prefix's docstring. Keyed by
# static_prefix_hash (skill+policy identity only), so any two compilations
# that pin the same skill/policy versions reuse the already-rendered text
# instead of re-joining/re-formatting it. Process-local, unbounded (the
# input space — distinct skill/policy version combinations actually in
# use — is small and slow-changing by construction; nothing here evicts,
# matching app/services/workflow_templates.py's own @lru_cache-without-
# eviction precedent for similarly small, static, file-backed catalogs).
_STATIC_PREFIX_CACHE: dict[str, str] = {}


def _cached_static_prefix(skill: Skill, policies: list[Policy]) -> tuple[str, str]:
    prefix_hash = _stable_hash(
        f"{skill.skill_key}@{skill.version}",
        *sorted(f"{p.policy_key}@{p.version}" for p in policies),
    )
    if prefix_hash not in _STATIC_PREFIX_CACHE:
        _STATIC_PREFIX_CACHE[prefix_hash] = _render_static_prefix(skill, policies)
    return prefix_hash, _STATIC_PREFIX_CACHE[prefix_hash]


@dataclass
class CompiledPrompt:
    """Every output this phase's instructions require, beyond the
    RuntimeInstructionPackage itself (see to_runtime_instruction_package)."""

    short_system_instruction: str
    task_instruction: str
    context_references: list[dict[str, Any]]
    policy_files: list[str]  # "policy_key@version" strings
    output_schema: dict[str, Any]
    runtime_configuration: dict[str, Any]
    token_allocation_report: dict[str, Any]
    compiler_hash: str
    static_prefix_hash: str
    skill_key: str
    skill_version: str
    lint_findings: list[LintFinding] = field(default_factory=list)

    def to_runtime_instruction_package(self, *, packet: WorkPacket) -> RuntimeInstructionPackage:
        return RuntimeInstructionPackage(
            packet=packet,
            system_instructions=f"{self.short_system_instruction}\n\n{self.task_instruction}",
            response_contract_description=self.output_schema.get("description", ""),
            expected_output_schema_ref=f"prompt-compiler:{self.skill_key}@{self.skill_version}",
            generated_at=datetime.now(timezone.utc),
            generator_metadata={
                "compiler_hash": self.compiler_hash,
                "static_prefix_hash": self.static_prefix_hash,
                "skill_key": self.skill_key,
                "skill_version": self.skill_version,
                "policy_files": self.policy_files,
                "lint_finding_count": len(self.lint_findings),
            },
        )


class PromptCompiler:
    def compile(
        self,
        *,
        work_packet: WorkPacket,
        capability: RuntimeCapabilityManifest,
        profile: ProjectExecutionProfileRead,
        skill_version: str | None = None,
        policies: list[Policy] | None = None,
        rag_references: list[KnowledgeReference] | None = None,
    ) -> CompiledPrompt:
        task_type = work_packet.task_type.value
        skill = load_skill_for_task_type(task_type, version=skill_version)
        applicable_policies = policies if policies is not None else load_applicable_policies(task_type)
        rag = rag_references if rag_references is not None else list(work_packet.knowledge_context)

        static_prefix_hash, static_prefix_text = _cached_static_prefix(skill, applicable_policies)

        response_format = "structured_json" if task_type in _STRUCTURED_JSON_TASK_TYPES else "markdown_with_clarification_header"

        # capability.max_context_tokens/max_output_tokens are both required,
        # ge=1 fields — always a valid floor to min() against, so the
        # WorkPacket's own (optional) BudgetPolicy ceiling can only ever
        # tighten it, never loosen it past what the runtime actually supports.
        max_context_tokens = min(capability.max_context_tokens, work_packet.budget_policy.max_context_tokens or capability.max_context_tokens)
        max_output_tokens = min(capability.max_output_tokens, work_packet.budget_policy.max_output_tokens or capability.max_output_tokens)

        # --- Priority-tagged blocks — same primitives every real
        # ai_generation.py call already fits into a budget with (Phase 00
        # baseline section 4): P0 never dropped, P3+ droppable whole.
        objective_lines = [
            f"**Goal:** {work_packet.objective.goal}",
            f"**Success definition:** {work_packet.objective.success_definition}",
        ]
        if work_packet.objective.non_goals:
            objective_lines.append("**Non-goals:** " + "; ".join(work_packet.objective.non_goals))
        if work_packet.acceptance_criteria:
            objective_lines.append("**Acceptance criteria:**\n" + "\n".join(f"- [{c.id}] {c.description}" for c in work_packet.acceptance_criteria))

        blocks: list[ContextBlock] = [
            ContextBlock(priority="P0", label="objective", compressible=True, content="\n".join(objective_lines)),
            ContextBlock(priority="P0", label="skill_static_prefix", compressible=True, content=static_prefix_text),
        ]

        profile_lines = [f"**Working directories:** {', '.join(profile.working_directories) or '.'}"]
        for label, value in (
            ("Install", profile.install_command), ("Lint", profile.lint_command), ("Format check", profile.format_check_command),
            ("Type check", profile.type_check_command), ("Unit test", profile.unit_test_command),
            ("Integration test", profile.integration_test_command), ("Build", profile.build_command),
        ):
            if value:
                profile_lines.append(f"**{label} command:** `{value}`")
        if profile.branch_naming_convention:
            profile_lines.append(f"**Branch naming:** {profile.branch_naming_convention}")
        if profile.commit_convention:
            profile_lines.append(f"**Commit convention:** {profile.commit_convention}")
        blocks.append(ContextBlock(priority="P1", label="execution_profile", compressible=True, content="\n".join(profile_lines)))

        if work_packet.upstream_artifacts:
            for artifact in work_packet.upstream_artifacts:
                content = f"## {artifact.artifact_type} ({artifact.status})\n{artifact.summary or '(no summary available)'}"
                blocks.append(ContextBlock(priority="P2", label=f"artifact:{artifact.artifact_id}", compressible=True, content=content))

        for chunk in rag:
            blocks.append(ContextBlock(priority="P3", label=f"rag:{chunk.chunk_id}", content=f"## Source: {chunk.source_title}\n{chunk.snippet}"))

        budget_result = TokenBudgetService(context_token_budget=max_context_tokens, output_token_budget=max_output_tokens).build(blocks)
        fitted_by_label = {b.label: b for b in budget_result.blocks}

        def _content(label: str) -> str:
            fitted = fitted_by_label.get(label)
            return fitted.content if fitted and fitted.included else ""

        task_instruction = "\n\n".join(
            part for part in (
                _content("objective"), _content("skill_static_prefix"), _content("execution_profile"),
                *(_content(f"artifact:{a.artifact_id}") for a in work_packet.upstream_artifacts),
                *(_content(f"rag:{c.chunk_id}") for c in rag),
            ) if part
        )

        short_system_instruction = (
            f"You are executing the '{skill.name}' skill (v{skill.version}) for task type {task_type}. "
            f"{skill.purpose.strip()} Respond using the {response_format} contract. "
            "Follow every guardrail in the Guardrails section below without exception."
        )

        allowed_tool_categories = sorted(set(skill.allowed_tool_categories) & set(capability.supported_tool_categories))
        runtime_configuration = {
            "response_format": response_format,
            "allowed_tool_categories": allowed_tool_categories,
            "allowed_command_patterns": list(profile.allowed_command_patterns),
            "denied_command_patterns": dedupe_instructions(list(profile.denied_command_patterns)),
            "allowed_paths": list(profile.allowed_paths),
            "denied_paths": dedupe_instructions(list(profile.denied_paths)),
            "network_policy": profile.network_policy.model_dump(),
            "max_context_tokens": max_context_tokens,
            "max_output_tokens": max_output_tokens,
            "runtime_name": capability.runtime_name,
        }

        output_schema = {
            "format": response_format,
            "description": skill.output_contract,
        }

        policy_files = sorted(f"{p.policy_key}@{p.version}" for p in applicable_policies)
        token_allocation_report = budget_result.to_report_dict()

        rendered_text_for_lint = short_system_instruction + "\n\n" + task_instruction
        lint_findings = lint_compiled_text(
            skill=skill, policies=applicable_policies, rendered_text=rendered_text_for_lint,
            data_classification=profile.data_classification, network_default=profile.network_policy.default,
            supported_tool_categories=capability.supported_tool_categories,
        )
        raise_if_blocking(lint_findings)

        compiler_hash = _stable_hash(
            static_prefix_hash, task_instruction, json.dumps(runtime_configuration, sort_keys=True),
            json.dumps(output_schema, sort_keys=True), work_packet.task_type.value,
        )

        context_references = [
            {"kind": "artifact", "artifact_id": str(a.artifact_id), "artifact_type": a.artifact_type} for a in work_packet.upstream_artifacts
        ] + [
            {"kind": "knowledge_chunk", "chunk_id": c.chunk_id, "source_title": c.source_title} for c in rag
        ]

        return CompiledPrompt(
            short_system_instruction=short_system_instruction,
            task_instruction=task_instruction,
            context_references=context_references,
            policy_files=policy_files,
            output_schema=output_schema,
            runtime_configuration=runtime_configuration,
            token_allocation_report=token_allocation_report,
            compiler_hash=compiler_hash,
            static_prefix_hash=static_prefix_hash,
            skill_key=skill.skill_key,
            skill_version=skill.version,
            lint_findings=lint_findings,
        )
