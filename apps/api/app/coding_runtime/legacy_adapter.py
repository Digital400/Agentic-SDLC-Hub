"""LegacyDocumentRuntimeAdapter / LegacyCodingRuntimeAdapter — wraps
today's exact, unmodified generation paths (app.services.ai_generation.
generate() and app.services.implementation_agent.run_implementation_agent())
behind the Phase 02 adapter interface. See app/coding_runtime/__init__.py.

"Route the existing harness through LegacyRuntimeAdapter by default. Do
not change existing external behavior." — these two classes are a
TRANSLATION layer only: every real call underneath is the exact same
function this codebase already calls today (Phase 00 baseline sections
1.1-1.4), unmodified. No business agent is repointed at this module by
this phase — see the module docstring below on why a WorkPacket alone
can't drive either legacy path, and app.agent_runtime's own module
docstring's "do not connect an external runtime yet" (Phase 01, still
true through Phase 02).

A WorkPacket deliberately carries REFERENCES (hard rule 3), not a
resolved Project/WorkflowNode/AgentPrompt/ImplementationTask/RepoContext
— resolving those from a WorkPacket's references against this
application's own APIs is future work (the baseline doc's section 11
names the existing endpoints a resolver would call), not something this
phase re-derives. Both adapters below are therefore constructed with
their real context ALREADY resolved by the caller — the same division of
labor app.prompt_compiler.PromptCompiler already established between
"what compiles instructions" and "what resolves a WorkPacket's
references," for the identical reason.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent_runtime import ExecutionResult, UsageEvidence, WorkPacket
from app.agent_runtime.capability import RuntimeCapability, RuntimeCapabilityManifest
from app.agent_runtime.enums import ExecutionState
from app.agent_runtime.execution_result import ClarificationRequest, FileChangeEvidence
from app.coding_runtime.base import CodingRuntimeAdapter, DocumentRuntimeAdapter
from app.coding_runtime.enums import ExecutionLocation, RuntimeKind
from app.coding_runtime.manifest import RuntimeDefinition, RuntimeHealth
from app.models import AgentPrompt, AgentPromptRole, Project, Story, WorkflowNode
from app.models.implementation_task import ImplementationTask
from app.services.ai_generation import CLARIFICATION_MARKER, generate, get_active_provider
from app.services.implementation_agent import ImplementationAgentResult, run_implementation_agent
from app.services.repo_context_builder import RepoContextPreviewResult
from app.services.retrieval import RetrievedChunk


def _legacy_health(runtime_name: str) -> RuntimeHealth:
    """Both legacy adapters run in-process (no separate service to
    probe), so "healthy" here means "a provider path resolves at all" —
    mirrors app.services.ai_generation.is_ai_configured's own framing:
    the mock fallback always makes this codebase runnable, so this is
    never unhealthy, only possibly degraded to mock."""
    provider = get_active_provider()
    return RuntimeHealth(
        runtime_name=runtime_name, healthy=True, checked_at=datetime.now(timezone.utc),
        detail=f"In-process — active provider: {provider}." + (" (mock fallback — no real provider key configured)" if provider == "mock" else ""),
    )


class LegacyDocumentRuntimeAdapter(DocumentRuntimeAdapter):
    """Wraps app.services.ai_generation.generate() — every DRAFT/IMPROVE/
    VALIDATE project-level run and every story_lld_agent.py call (Phase
    00 baseline sections 1.1-1.3) goes through this exact function today.
    """

    def __init__(
        self,
        *,
        project: Project,
        node: WorkflowNode,
        action: AgentPromptRole,
        active_prompt: AgentPrompt,
        approved_artifact_content: dict[str, str],
        approved_artifact_summaries: dict[str, str],
        context_token_budget: int,
        output_token_budget: int,
        full_content_artifact_types: set[str] | None = None,
        retrieved_chunks: list[RetrievedChunk] | None = None,
        review_comments: list[str] | None = None,
        current_draft_content: str | None = None,
    ) -> None:
        self.definition = RuntimeDefinition(
            name="legacy-document", kind=RuntimeKind.DOCUMENT, execution_location=ExecutionLocation.COMPANY_SANDBOX,
            description="Wraps app.services.ai_generation.generate() unmodified.",
            capability=RuntimeCapabilityManifest(
                runtime_name="legacy-document", max_context_tokens=context_token_budget, max_output_tokens=output_token_budget,
                supports_structured_output=False, supports_streaming=False,
                response_formats=["markdown_with_clarification_header"],
                capabilities=[RuntimeCapability.HUMAN_APPROVAL, RuntimeCapability.USAGE_REPORTING],
            ),
        )
        self._project = project
        self._node = node
        self._action = action
        self._active_prompt = active_prompt
        self._approved_artifact_content = approved_artifact_content
        self._approved_artifact_summaries = approved_artifact_summaries
        self._context_token_budget = context_token_budget
        self._output_token_budget = output_token_budget
        self._full_content_artifact_types = full_content_artifact_types
        self._retrieved_chunks = retrieved_chunks
        self._review_comments = review_comments
        self._current_draft_content = current_draft_content

    def execute(self, packet: WorkPacket) -> ExecutionResult:
        started_at = datetime.now(timezone.utc)
        result = generate(
            project=self._project, node=self._node, action=self._action, active_prompt=self._active_prompt,
            approved_artifact_content=self._approved_artifact_content, approved_artifact_summaries=self._approved_artifact_summaries,
            freeform_context={"goal": packet.objective.goal, "success_definition": packet.objective.success_definition},
            context_token_budget=self._context_token_budget, output_token_budget=self._output_token_budget,
            full_content_artifact_types=self._full_content_artifact_types, retrieved_chunks=self._retrieved_chunks,
            review_comments=self._review_comments, current_draft_content=self._current_draft_content,
        )
        completed_at = datetime.now(timezone.utc)

        usage = UsageEvidence(
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
            llm_calls_made=1, wall_clock_seconds=(completed_at - started_at).total_seconds(),
        )
        # cost=0.0 for the mock path is a real, meaningful value (Phase
        # 01's UsageEvidence.cost_usd docstring: 0.0 isn't a stand-in for
        # "unknown"); a real provider call's own cost is always computed,
        # never estimated, by ai_generation.py itself. KNOWN GAP: the
        # Phase 05 model-gateway path can return cost=None ("genuinely
        # unknown" — see AgentGenerationResult.cost's own docstring), but
        # UsageEvidence.cost_usd (Phase 01) has no "unknown" representation
        # of its own (a non-Optional float defaulting to 0.0) — that's a
        # Phase 01 contract gap, not something this translation layer can
        # fix; falling back to 0.0 here is the least-wrong option available
        # today, not a claim that 0.0 is the real cost.
        usage.cost_usd = result.cost if result.cost is not None else 0.0

        if result.content_markdown.startswith(CLARIFICATION_MARKER):
            return ExecutionResult(
                result_id=packet.packet_id, packet_id=packet.packet_id, state=ExecutionState.CLARIFICATION_REQUIRED,
                summary="The legacy document runtime needs more information before it can draft this artifact.",
                clarification=ClarificationRequest(
                    questions=result.clarification_questions or ["(no specific questions were returned)"], blocking=True,
                ),
                usage=usage, started_at=started_at, completed_at=completed_at,
            )

        return ExecutionResult(
            result_id=packet.packet_id, packet_id=packet.packet_id, state=ExecutionState.COMPLETED,
            summary=result.content_markdown[:280],
            file_changes=[],  # a document-kind runtime drafts artifact content, not repository files
            usage=usage, started_at=started_at, completed_at=completed_at,
            extensions={"legacy": {"content_markdown": result.content_markdown, "used_mock": result.used_mock}},
        )

    def check_health(self) -> RuntimeHealth:
        return _legacy_health("legacy-document")


class LegacyCodingRuntimeAdapter(CodingRuntimeAdapter):
    """Wraps app.services.implementation_agent.run_implementation_agent()
    — the exact function app/api/routes/implementation_runs.py's
    start_implementation_run already calls today (Phase 00 baseline
    section 1.4). Proposes a diff for human review; never writes to a
    repository, branch, or PR itself — same HARD RULE that module's own
    docstring states, carried through unchanged."""

    def __init__(
        self,
        *,
        task: ImplementationTask,
        repo_context: RepoContextPreviewResult,
        story: Story | None,
        lld_summary: str,
        standards_chunks: list[RetrievedChunk] | None = None,
        jira_issue_key: str | None = None,
    ) -> None:
        self.definition = RuntimeDefinition(
            name="legacy-coding", kind=RuntimeKind.CODING, execution_location=ExecutionLocation.COMPANY_SANDBOX,
            description="Wraps app.services.implementation_agent.run_implementation_agent() unmodified.",
            capability=RuntimeCapabilityManifest(
                runtime_name="legacy-coding", max_context_tokens=16000, max_output_tokens=16000,
                supported_tool_categories=["file_read"], supports_structured_output=True,
                response_formats=["structured_json"],
                # Deliberately NOT SHELL_EXECUTION/TEST_EXECUTION/SANDBOX —
                # this legacy path proposes a diff via reasoning alone; it
                # never runs a real command (Phase 00 baseline section 8's
                # "no test-execution sandbox" finding applies here too).
                capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION, RuntimeCapability.HUMAN_APPROVAL, RuntimeCapability.USAGE_REPORTING],
            ),
        )
        self._task = task
        self._repo_context = repo_context
        self._story = story
        self._lld_summary = lld_summary
        self._standards_chunks = standards_chunks
        self._jira_issue_key = jira_issue_key

    def execute(self, packet: WorkPacket) -> ExecutionResult:
        started_at = datetime.now(timezone.utc)
        result: ImplementationAgentResult = run_implementation_agent(
            task=self._task, repo_context=self._repo_context, story=self._story, lld_summary=self._lld_summary,
            standards_chunks=self._standards_chunks, jira_issue_key=self._jira_issue_key,
        )
        completed_at = datetime.now(timezone.utc)

        file_changes = [
            FileChangeEvidence(path=c.path, change_type=c.change_type, summary=c.summary or "(no summary)", diff=None)
            for c in result.proposed_file_changes
        ]

        usage = UsageEvidence(
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
            llm_calls_made=1, wall_clock_seconds=(completed_at - started_at).total_seconds(),
        )
        usage.cost_usd = result.cost

        return ExecutionResult(
            result_id=packet.packet_id, packet_id=packet.packet_id, state=ExecutionState.WAITING_APPROVAL,
            summary=result.explanation[:280] or f"Proposed {len(file_changes)} file change(s) for {self._task.title!r}.",
            file_changes=file_changes, usage=usage, started_at=started_at, completed_at=completed_at,
            extensions={
                "legacy": {
                    "diff_text": result.diff_text, "test_command": result.test_command, "risks": result.risks,
                    "pr_description": result.pr_description, "used_mock": result.used_mock,
                },
            },
        )

    def check_health(self) -> RuntimeHealth:
        return _legacy_health("legacy-coding")
