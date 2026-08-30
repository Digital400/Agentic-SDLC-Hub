"""Real AI generation for agent runs, via the Anthropic API or Gemini API.

Falls back to app/services/mock_agent.py's deterministic placeholder when
neither ANTHROPIC_API_KEY nor GEMINI_API_KEY is configured (see
apps/api/.env.example), so the system stays fully testable and demoable
without any paid key. When both are set, Anthropic takes priority. Gemini
is offered as a free-tier-friendly alternative (Google AI Studio issues
Gemini API keys with a real free tier) for anyone who wants real generated
output without Anthropic billing — see `_generate_with_gemini`.

The model is instructed to respond in a fixed two-part format — a one-line
JSON header (needs_clarification + questions) followed by "---" followed by
the actual Markdown content — so the caller can reliably tell "here is your
draft" apart from "I don't have enough information to draft this" without
depending on a beta structured-output API shape. See `_parse_response`.
This instruction is provider-agnostic — both `_build_system_prompt` and
`build_input_context` are plain text assembly, shared by both providers.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import get_settings
from app.models import AgentPrompt, AgentPromptRole, Project, WorkflowNode
from app.services.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

# A run whose output starts with this heading is a clarification request,
# not a draft — checked by app/api/routes/agent_runs.py's save-to-artifact
# step to avoid ever marking clarification-needed output "ready for review".
CLARIFICATION_MARKER = "# Clarification Needed"

# Published per-1M-token rates for the models this app is expected to use
# (see the claude-api skill's cached pricing table). Real usage x real
# pricing — unlike mock_agent.py's placeholder cost, this is an accurate
# estimate once a real call has actually been made. No entry for Gemini
# models: they're offered here specifically as the free-tier option (see
# module docstring), so cost is reported as 0.0 for that provider rather
# than guessing at a paid-tier rate that may not even apply to the caller's
# account — see `generate`'s Gemini branch.
_MODEL_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

AIProvider = Literal["anthropic", "gemini", "mock"]


class AIGenerationError(Exception):
    """Raised when the underlying provider's API call itself fails (auth,
    rate limit, network, ...). Callers should record this as a FAILED
    agent run rather than let it become an unhandled 500."""


@dataclass
class AgentGenerationResult:
    content_markdown: str
    needs_clarification: bool
    clarification_questions: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    used_mock: bool = False


def get_active_provider() -> AIProvider:
    """Anthropic takes priority when both keys are set — see module
    docstring. `AgentRun.token_usage`/`used_mock` don't currently record
    which real provider generated an output, only real-vs-mock; the actual
    provider is visible in the run's audit log entry instead."""
    settings = get_settings()
    if settings.ANTHROPIC_API_KEY:
        return "anthropic"
    if settings.GEMINI_API_KEY:
        return "gemini"
    return "mock"


def is_ai_configured() -> bool:
    return get_active_provider() != "mock"


def build_input_context(
    *,
    project: Project,
    node: WorkflowNode,
    approved_inputs: dict[str, str],
    freeform_context: dict[str, Any],
    retrieved_chunks: list[RetrievedChunk] | None = None,
    validation_feedback: list[str] | None = None,
) -> str:
    """Assembles the user-turn content: project framing, the current
    stage's own metadata, every approved upstream artifact's full content,
    any freeform context (e.g. a stakeholder request with no artifact), and
    — when retrieval found anything relevant (see
    app/services/retrieval.py) — a Knowledge Base section the agent is
    instructed to cite by source title. When retrieval found nothing, that
    section is omitted entirely and the agent proceeds on project context
    alone, per the product's stated fallback rule.
    """
    lines = [
        f"# Project: {project.name}",
        f"Business owner: {project.business_owner}",
        f"Project description: {project.description or 'Not provided.'}",
        "",
        f"# Current stage: {node.name} (`{node.node_key}`)",
        node.description,
        f"Output artifact type to produce: `{node.output_artifact_type}`",
        "",
    ]

    if freeform_context:
        lines.append("# Additional context")
        for key, value in freeform_context.items():
            lines.append(f"**{key}:** {value}")
        lines.append("")

    if validation_feedback:
        # A loop-engine IMPROVE pass (see app/services/loop_engine.py) —
        # the previous VALIDATE step's issues, so the model addresses them
        # explicitly rather than regenerating from scratch and hoping.
        lines.append("# Feedback to address from the previous validation pass")
        lines.append("This is a revision. Address every item below in your updated draft:")
        for item in validation_feedback:
            lines.append(f"- {item}")
        lines.append("")

    if approved_inputs:
        lines.append("# Approved upstream artifacts (your required inputs)")
        for artifact_type, content in approved_inputs.items():
            lines.append(f"## {artifact_type}")
            lines.append(content)
            lines.append("")

    if retrieved_chunks:
        lines.append("# Internal Knowledge Base — relevant excerpts")
        lines.append(
            "These were retrieved because they may be relevant to this project, stage, or "
            "input. When you use information from one of them, cite it inline by its source "
            "title in parentheses, e.g. (Source: Company Engineering Handbook). Do not cite a "
            "source you didn't actually use."
        )
        for chunk in retrieved_chunks:
            lines.append(f"## Source: {chunk.source_title}")
            lines.append(chunk.content)
            lines.append("")
    else:
        lines.append(
            "# Internal Knowledge Base\nNo relevant internal knowledge was found for this run — "
            "proceed using only the project context above.\n"
        )

    lines.append(
        "Using only the above, draft this stage's output exactly per your Output Format "
        "instructions below. Generate an artifact for THIS stage only — do not draft "
        "content for any other workflow stage. If the information above is insufficient "
        "to draft this confidently, do not guess — ask clarification questions instead, "
        "per the response format below."
    )
    return "\n".join(lines)


def _build_system_prompt(active_prompt: AgentPrompt) -> str:
    checklist = "\n".join(f"- {item}" for item in active_prompt.validation_checklist) or "(none specified)"
    return (
        f"{active_prompt.system_prompt}\n\n"
        f"## Output format\n{active_prompt.output_format}\n\n"
        f"## Quality checklist — self-check before responding\n{checklist}\n\n"
        "## Citing internal knowledge\n"
        "The input may include an \"Internal Knowledge Base\" section with excerpts from the "
        "company's own knowledge sources. If it's present and you draw on it, cite the source "
        "by its title in parentheses at the point you use it. If that section says no relevant "
        "knowledge was found, do not invent a citation — proceed on project context alone.\n\n"
        "## Response format (follow exactly)\n"
        "Respond with exactly two parts, in order:\n"
        "1. A single-line JSON object: "
        '{"needs_clarification": true|false, "clarification_questions": ["...", ...]}\n'
        "   (empty array when needs_clarification is false)\n"
        "2. A line containing only: ---\n"
        "3. Then either your full drafted output per the Output Format above (when "
        "needs_clarification is false), or nothing further (when true) — do not draft a "
        "partial or best-guess document in that case.\n\n"
        "Only set needs_clarification to true when the input genuinely lacks information "
        "you need — not merely because more detail would be nice to have."
    )


def _parse_response(raw_text: str) -> tuple[bool, list[str], str]:
    """Parses the two-part response format instructed above. Falls back to
    treating the whole response as content if the model didn't follow the
    format — a formatting slip shouldn't crash the run."""
    if "---" in raw_text:
        header, _, body = raw_text.partition("---")
        try:
            parsed = json.loads(header.strip())
            needs_clarification = bool(parsed.get("needs_clarification", False))
            questions = [str(q) for q in parsed.get("clarification_questions", [])]
            return needs_clarification, questions, body.strip()
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Agent response header was not valid JSON; treating the whole response as content.")
    return False, [], raw_text.strip()


def format_clarification_output(questions: list[str]) -> str:
    lines = [CLARIFICATION_MARKER, "", "The agent needs more information before it can draft this artifact:", ""]
    lines += [f"- {q}" for q in questions] if questions else ["- (No specific questions were returned.)"]
    return "\n".join(lines)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_rate, output_rate = _MODEL_PRICING_PER_MTOK.get(model, (0.0, 0.0))
    return round((prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000, 6)


def _generate_with_anthropic(system_prompt: str, user_content: str) -> AgentGenerationResult:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as exc:
        raise AIGenerationError(str(exc)) from exc

    raw_text = "".join(block.text for block in response.content if block.type == "text")
    needs_clarification, questions, body = _parse_response(raw_text)
    content_markdown = format_clarification_output(questions) if needs_clarification else body

    prompt_tokens = response.usage.input_tokens
    completion_tokens = response.usage.output_tokens

    return AgentGenerationResult(
        content_markdown=content_markdown,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cost=_estimate_cost(settings.AI_MODEL, prompt_tokens, completion_tokens),
        used_mock=False,
    )


def _generate_with_gemini(system_prompt: str, user_content: str) -> AgentGenerationResult:
    settings = get_settings()
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=user_content,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=settings.AI_MAX_TOKENS,
            ),
        )
    except genai_errors.APIError as exc:
        raise AIGenerationError(str(exc)) from exc

    raw_text = response.text or ""
    needs_clarification, questions, body = _parse_response(raw_text)
    content_markdown = format_clarification_output(questions) if needs_clarification else body

    usage = response.usage_metadata
    prompt_tokens = (usage.prompt_token_count if usage else None) or 0
    completion_tokens = (usage.candidates_token_count if usage else None) or 0

    return AgentGenerationResult(
        content_markdown=content_markdown,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        # See _MODEL_PRICING_PER_MTOK's comment — Gemini is offered as the
        # free-tier option, so cost is reported as 0.0 rather than assuming
        # a paid rate that may not apply to the caller's account.
        cost=0.0,
        used_mock=False,
    )


def generate(
    *,
    project: Project,
    node: WorkflowNode,
    action: AgentPromptRole,
    active_prompt: AgentPrompt,
    approved_inputs: dict[str, str],
    freeform_context: dict[str, Any],
    retrieved_chunks: list[RetrievedChunk] | None = None,
    iteration: int = 1,
    validation_feedback: list[str] | None = None,
) -> AgentGenerationResult:
    """Generates this stage's output — via Anthropic or Gemini if either is
    configured, otherwise the deterministic mock (see module docstring).
    `retrieved_chunks` (see app/services/retrieval.py) is injected into
    context either way — even the mock path notes how many chunks it saw,
    so the "retrieved sources" behavior is observable without a paid key.

    `iteration`/`validation_feedback` are for app/services/loop_engine.py's
    IMPROVE step — a revision pass, not a fresh draft. `iteration` only
    affects the mock path (see mock_agent.generate_mock_output's
    docstring); `validation_feedback` is included in a real call's context
    either way."""
    provider = get_active_provider()

    if provider == "mock":
        # Local import avoids a hard dependency the other direction (mock
        # generation has no reason to know about real generation).
        from app.services import mock_agent

        output_text = mock_agent.generate_mock_output(
            agent_key=active_prompt.agent_definition.agent_key,
            node=node,
            action=action,
            input_artifact_count=len(approved_inputs),
            retrieved_chunks=retrieved_chunks or [],
            iteration=iteration,
            validation_feedback=validation_feedback,
        )
        token_usage, cost = mock_agent.estimate_mock_usage(prompt_text=active_prompt.system_prompt, output_text=output_text)
        return AgentGenerationResult(
            content_markdown=output_text,
            needs_clarification=False,
            prompt_tokens=token_usage["prompt_tokens"],
            completion_tokens=token_usage["completion_tokens"],
            total_tokens=token_usage["total_tokens"],
            cost=cost,
            used_mock=True,
        )

    system_prompt = _build_system_prompt(active_prompt)
    user_content = build_input_context(
        project=project,
        node=node,
        approved_inputs=approved_inputs,
        freeform_context=freeform_context,
        retrieved_chunks=retrieved_chunks,
        validation_feedback=validation_feedback,
    )

    if provider == "gemini":
        return _generate_with_gemini(system_prompt, user_content)
    return _generate_with_anthropic(system_prompt, user_content)
