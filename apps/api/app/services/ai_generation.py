"""Real AI generation for agent runs, via the Anthropic API, Gemini API,
NVIDIA's hosted "Build" API, or Ollama.

Falls back to app/services/mock_agent.py's deterministic placeholder when
none of ANTHROPIC_API_KEY, GEMINI_API_KEY, NVIDIA_API_KEY, or Ollama is
configured (see apps/api/.env.example), so the system stays fully
testable and demoable without any paid key. Priority order:
  1. Anthropic (if ANTHROPIC_API_KEY is set)
  2. Gemini (if GEMINI_API_KEY is set)
  3. NVIDIA's hosted "Build" API (if NVIDIA_API_KEY is set)
  4. Ollama (if running locally, no API key needed)
  5. Mock (deterministic fallback)

Gemini and NVIDIA are both offered as free-tier-friendly alternatives for
anyone who wants real generated output without Anthropic billing (Google
AI Studio and build.nvidia.com both issue free-tier API keys). NVIDIA sits
ahead of Ollama in priority specifically because it's a fast hosted call
rather than local CPU inference — see `_generate_with_ollama`'s own
module-docstring-adjacent note that CPU-only local inference can
legitimately take minutes per call. Ollama is completely free and runs
locally — install Ollama from https://ollama.ai and pull a model (e.g.,
'ollama pull llama3.1:8b') to use it.

The model is instructed to respond in a fixed two-part format — a one-line
JSON header (needs_clarification + questions) followed by "---" followed by
the actual Markdown content — so the caller can reliably tell "here is your
draft" apart from "I don't have enough information to draft this" without
depending on a beta structured-output API shape. See `_parse_response`.
This instruction is provider-agnostic — both `_build_system_prompt` and
`build_prioritized_context` are plain text assembly, shared by all
providers.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
import httpx
import ollama
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import get_settings
from app.models import AgentPrompt, AgentPromptRole, Project, WorkflowNode
from app.services.retrieval import RetrievedChunk
from app.services.token_budget import ContextBlock, TokenBudgetResult, TokenBudgetService

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

# Ollama's own Python client wraps httpx and accepts no timeout by
# default — an unresponsive/still-loading local Ollama would otherwise
# hang a request indefinitely, with no error surfaced anywhere (the
# frontend just spins forever). Two different budgets: the connectivity
# check in get_active_provider should fail fast (it runs on the hot path
# of every single agent-run/approve request), while an actual generation
# call needs real headroom — CPU-only inference of even a small model can
# legitimately take minutes for a few thousand output tokens.
_OLLAMA_CONNECTIVITY_TIMEOUT_SECONDS = 5.0
_OLLAMA_GENERATION_TIMEOUT_SECONDS = 180.0

# NVIDIA's hosted API — a real network call to a remote service, same
# generous-but-bounded reasoning as the Anthropic/Gemini SDK calls below
# (which have their own client-level defaults); httpx needs an explicit one.
# Deliberately NOT unbounded: this call happens synchronously inside a web
# request (an agent run or Approve), so an indefinite timeout would mean a
# genuinely stuck endpoint hangs every future request forever instead of
# failing over to the heuristic fallback in bounded time. 300s is generous
# for a large/cold-starting hosted model without approaching "forever."
_NVIDIA_REQUEST_TIMEOUT_SECONDS = 300.0

AIProvider = Literal["anthropic", "gemini", "nvidia", "ollama", "mock"]


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
    # Token Budget Service output (see app/services/token_budget.py) — the
    # pre-call estimate and the full per-block report, for AgentRun's
    # estimated-vs-actual token fields and its token_budget_report.
    estimated_context_tokens: int = 0
    token_budget_report: dict = field(default_factory=dict)


def get_active_provider() -> AIProvider:
    """Anthropic takes priority when both keys are set — see module
    docstring. `AgentRun.token_usage`/`used_mock` don't currently record
    which real provider generated an output, only real-vs-mock; the actual
    provider is visible in the run's audit log entry instead.
    
    Provider priority:
    1. Anthropic (if ANTHROPIC_API_KEY is set)
    2. Gemini (if GEMINI_API_KEY is set)
    3. NVIDIA's hosted "Build" API (if NVIDIA_API_KEY is set)
    4. Ollama (if running locally, no API key needed)
    5. Mock (deterministic fallback)
    """
    settings = get_settings()
    if settings.ANTHROPIC_API_KEY:
        return "anthropic"
    if settings.GEMINI_API_KEY:
        return "gemini"
    if settings.NVIDIA_API_KEY:
        return "nvidia"
    # Check if Ollama is available by attempting to connect
    try:
        client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=_OLLAMA_CONNECTIVITY_TIMEOUT_SECONDS)
        # Quick connectivity check - list models to verify Ollama is running
        client.list()
        return "ollama"
    except Exception:
        # Ollama not available, fall back to mock
        pass
    return "mock"


def is_ai_configured() -> bool:
    return get_active_provider() != "mock"


# Fallback only — used when an approved artifact has no
# agent_context_summary yet (e.g. it was approved before
# app/services/artifact_summary.py existed, or summarization failed and
# was never retried). Real summaries come from that service, generated
# whenever an artifact is approved — see app/api/routes/reviews.py.
_SUMMARY_FALLBACK_MAX_CHARS = 800


def _fallback_summary(content: str, max_chars: int = _SUMMARY_FALLBACK_MAX_CHARS) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "\n…(not yet summarized — showing a plain truncation)"


def build_prioritized_context(
    *,
    project: Project,
    node: WorkflowNode,
    approved_artifact_content: dict[str, str],
    approved_artifact_summaries: dict[str, str],
    freeform_context: dict[str, Any],
    context_token_budget: int,
    output_token_budget: int,
    full_content_artifact_types: set[str] | None = None,
    force_full_content: bool = False,
    retrieved_chunks: list[RetrievedChunk] | None = None,
    validation_feedback: list[str] | None = None,
    review_comments: list[str] | None = None,
    current_draft_content: str | None = None,
) -> TokenBudgetResult:
    """Assembles this run's context as priority-tagged blocks and fits them
    into `context_token_budget` via TokenBudgetService (see
    app/services/token_budget.py) — this is the "estimate token count
    before LLM call" + "prioritize context" + "compress or remove lower
    priority content" behavior, all in one pass, before any provider is
    ever called.

    `current_draft_content`, when set, is the artifact's own current
    version — the document being revised, as opposed to an *upstream*
    artifact (approved_artifact_content/summaries are always some other
    stage's output). Set this for a genuine revision pass (see
    app/services/revision_agent.py) so the model edits a real document
    instead of drafting one from nothing; it also switches node_rules into
    explicit "revise, don't redraft" instructions.

    Priority mapping:
      P0 current user instruction     -> freeform_context (this run's own
                                          input, e.g. a stakeholder request)
                                          plus any loop-engine validation
                                          feedback to address (also a
                                          direct instruction for this call)
      P1 node rules                   -> project + stage framing (what
                                          this stage is, what it must
                                          produce)
      P2 approved artifact summaries  -> each approved upstream artifact's
                                          agent_context_summary (see
                                          app/services/artifact_summary.py)
                                          — the DEFAULT for every approved
                                          input
      P3 relevant RAG chunks          -> app/services/retrieval.py results
                                          (already stage-filtered and fit
                                          to the node's own max_rag_tokens
                                          budget there), one block per
                                          chunk, most similar first (so a
                                          dropped chunk here is always the
                                          least relevant one that made it
                                          past retrieval's own cap)
      P4 review comments              -> feedback from this node's most
                                          recent human review round, if any
      P5 full artifact content        -> ONLY escalated to for an approved
                                          input when one of three things is
                                          true (`_needs_full_content`):
                                            1. the node's own config says
                                               this stage directly depends
                                               on that artifact_type's
                                               detailed content
                                               (full_content_artifact_types)
                                            2. `force_full_content` — the
                                               run's action is IMPROVE (a
                                               human explicitly asked to
                                               revise this stage's
                                               artifact) or VALIDATE (a
                                               validator checking the full
                                               document) — see generate()
                                          Every other approved input stays
                                          at P2, even when one of them is
                                          escalated — "only if required"
                                          is decided per artifact, not
                                          all-or-nothing for the run.
    """
    full_content_artifact_types = full_content_artifact_types or set()
    blocks: list[ContextBlock] = []

    instruction_lines = []
    for key, value in freeform_context.items():
        instruction_lines.append(f"**{key}:** {value}")
    if validation_feedback:
        instruction_lines.append(
            "\nThis is a revision. Address every item below in your updated draft:"
        )
        instruction_lines += [f"- {item}" for item in validation_feedback]
    blocks.append(
        ContextBlock(priority="P0", label="current_instruction", content="\n".join(instruction_lines), compressible=True)
    )

    knowledge_note = (
        "No relevant internal knowledge was retrieved for this run — proceed using only the "
        "context provided, and do not invent a source citation."
        if not retrieved_chunks
        else "Relevant internal knowledge excerpts are included below (marked '## Source: ...'); "
        "cite one by its source title in parentheses if you use it."
    )
    node_rules = (
        f"# Project: {project.name}\n"
        f"Business owner: {project.business_owner}\n"
        f"Project description: {project.description or 'Not provided.'}\n\n"
        f"# Current stage: {node.name} (`{node.node_key}`)\n"
        f"{node.description}\n"
        f"Output artifact type to produce: `{node.output_artifact_type}`\n\n"
        f"{knowledge_note}\n\n"
    )
    if current_draft_content:
        node_rules += (
            "This is a REVISION of an existing document that a human reviewer sent back with "
            "feedback (see the current document and reviewer/validator feedback below). Update "
            "ONLY the section(s) affected by that feedback — copy every other section through "
            "unchanged, verbatim, in the same order and heading structure. Return the complete "
            "document (every section, in Markdown, using `## ` headings), not just the parts you "
            "changed, per your Output Format instructions."
        )
    else:
        node_rules += (
            "Using only the context provided, draft this stage's output exactly per your Output "
            "Format instructions. Generate an artifact for THIS stage only — do not draft content "
            "for any other workflow stage. If the information provided is insufficient to draft "
            "this confidently, do not guess — ask clarification questions instead, per the "
            "response format below."
        )
    blocks.append(ContextBlock(priority="P1", label="node_rules", content=node_rules, compressible=True))

    if current_draft_content:
        blocks.append(
            ContextBlock(
                priority="P1",
                label="current_draft",
                content=f"# Current document to revise\n{current_draft_content}",
                compressible=True,
            )
        )

    # P2 / P5 — see docstring: an approved input escalates to full content
    # only when this stage is configured to need it or the run's action
    # explicitly calls for it; every other approved input stays a summary.
    for artifact_type, full_content in approved_artifact_content.items():
        needs_full_content = force_full_content or artifact_type in full_content_artifact_types
        if needs_full_content:
            blocks.append(
                ContextBlock(priority="P5", label=f"full:{artifact_type}", content=f"## {artifact_type}\n{full_content}")
            )
        else:
            summary = approved_artifact_summaries.get(artifact_type) or _fallback_summary(full_content)
            blocks.append(
                ContextBlock(
                    priority="P2",
                    label=f"summary:{artifact_type}",
                    content=f"## {artifact_type} (summarized)\n{summary}",
                    compressible=True,
                )
            )

    for chunk in retrieved_chunks or []:
        blocks.append(
            ContextBlock(
                priority="P3",
                label=f"rag:{chunk.source_title}:{chunk.chunk_index}",
                content=f"## Source: {chunk.source_title}\n{chunk.content}",
            )
        )

    if review_comments:
        review_text = "# Feedback from the most recent human review\n" + "\n".join(
            f"- {c}" for c in review_comments
        )
        blocks.append(ContextBlock(priority="P4", label="review_comments", content=review_text))

    return TokenBudgetService(
        context_token_budget=context_token_budget, output_token_budget=output_token_budget
    ).build(blocks)


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


def _generate_with_anthropic(system_prompt: str, user_content: str, output_token_budget: int) -> AgentGenerationResult:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=output_token_budget,
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


def _generate_with_nvidia(system_prompt: str, user_content: str, output_token_budget: int) -> AgentGenerationResult:
    """Generate via NVIDIA's hosted "Build" API
    (https://build.nvidia.com) — a single OpenAI-compatible
    /v1/chat/completions endpoint fronting a catalog of hosted models
    (default: moonshotai/kimi-k3, see Settings.NVIDIA_MODEL). Plain httpx,
    not the `openai` SDK — this codebase has no OpenAI SDK dependency, and
    the endpoint is simple enough that adding one for a single call shape
    isn't worth it (same reasoning app/services/github_integration.py
    applies to using plain httpx over a GitHub SDK)."""
    settings = get_settings()

    try:
        response = httpx.post(
            f"{settings.NVIDIA_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.NVIDIA_API_KEY}", "Accept": "application/json"},
            json={
                "model": settings.NVIDIA_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": output_token_budget,
                "stream": False,
            },
            timeout=_NVIDIA_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Covers both a network failure and a non-2xx response (via
        # raise_for_status) — never includes the Authorization header or
        # API key, only httpx's own exception message.
        raise AIGenerationError(f"NVIDIA generation failed: {exc}") from exc

    data = response.json()
    raw_text = data["choices"][0]["message"]["content"] or ""
    needs_clarification, questions, body = _parse_response(raw_text)
    content_markdown = format_clarification_output(questions) if needs_clarification else body

    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0

    return AgentGenerationResult(
        content_markdown=content_markdown,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage.get("total_tokens", prompt_tokens + completion_tokens) or (prompt_tokens + completion_tokens),
        # NVIDIA's Build API issues free-tier keys for prototyping, same
        # framing as Gemini's own free-tier cost=0.0 above — not assuming a
        # paid rate that may not apply to the caller's account/quota.
        cost=0.0,
        used_mock=False,
    )


def _generate_with_gemini(system_prompt: str, user_content: str, output_token_budget: int) -> AgentGenerationResult:
    settings = get_settings()
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=user_content,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=output_token_budget,
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


def _generate_with_ollama(system_prompt: str, user_content: str, output_token_budget: int) -> AgentGenerationResult:
    """Generate using Ollama local inference. Completely free, no API key
    needed — requires Ollama to be running locally (default:
    http://localhost:11434) with the configured model pulled.
    
    PERFORMANCE: Optimized for CPU inference with reduced context window
    and faster sampling parameters."""
    settings = get_settings()
    client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=_OLLAMA_GENERATION_TIMEOUT_SECONDS)

    try:
        # Ollama's chat API accepts messages in a similar format to OpenAI
        response = client.chat(
            model=settings.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            options={
                "num_predict": output_token_budget,  # Max output tokens
                "num_ctx": 4096,  # Reduced context window for faster CPU processing
                "temperature": 0.7,  # Slightly lower for faster, more focused output
                "top_p": 0.9,  # Nucleus sampling for speed
                "top_k": 40,  # Limit choices for faster sampling
                "repeat_penalty": 1.1,  # Prevent loops
                "num_thread": 8,  # Use more CPU threads for parallel processing
            },
        )
    except Exception as exc:
        # Includes a timeout (httpx.TimeoutException, via ollama's client) —
        # surfaced as a normal AIGenerationError so every existing caller's
        # fallback-to-heuristic/mock path handles it exactly like any other
        # provider failure, rather than the request hanging indefinitely.
        raise AIGenerationError(f"Ollama generation failed: {str(exc)}") from exc

    raw_text = response.get("message", {}).get("content", "")
    needs_clarification, questions, body = _parse_response(raw_text)
    content_markdown = format_clarification_output(questions) if needs_clarification else body

    # Ollama returns token counts in the response
    prompt_tokens = response.get("prompt_eval_count", 0)
    completion_tokens = response.get("eval_count", 0)

    return AgentGenerationResult(
        content_markdown=content_markdown,
        needs_clarification=needs_clarification,
        clarification_questions=questions,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        # Ollama is completely free (runs locally), so cost is 0.0
        cost=0.0,
        used_mock=False,
    )


def generate(
    *,
    project: Project,
    node: WorkflowNode,
    action: AgentPromptRole,
    active_prompt: AgentPrompt,
    approved_artifact_content: dict[str, str],
    approved_artifact_summaries: dict[str, str],
    freeform_context: dict[str, Any],
    context_token_budget: int,
    output_token_budget: int,
    full_content_artifact_types: set[str] | None = None,
    retrieved_chunks: list[RetrievedChunk] | None = None,
    review_comments: list[str] | None = None,
    iteration: int = 1,
    validation_feedback: list[str] | None = None,
    current_draft_content: str | None = None,
) -> AgentGenerationResult:
    """Generates this stage's output — via Anthropic or Gemini if either is
    configured, otherwise the deterministic mock (see module docstring).
    `retrieved_chunks` (see app/services/retrieval.py) is injected into
    context either way — even the mock path notes how many chunks it saw,
    so the "retrieved sources" behavior is observable without a paid key.

    `context_token_budget`/`output_token_budget` come from the node's own
    config (see app/models/workflow.py) and are always applied — via
    build_prioritized_context (see app/services/token_budget.py) — even on
    the mock path, so a run's estimated-tokens/token-budget-report fields
    are meaningful regardless of provider.

    `approved_artifact_summaries` (see app/services/artifact_summary.py)
    is what build_prioritized_context uses by DEFAULT for an approved
    input; `approved_artifact_content` (full text) is only used when
    `full_content_artifact_types` names that artifact_type (this stage
    directly depends on its detail — set on the node) or when `action` is
    IMPROVE (a human explicitly asked to revise this stage's own artifact)
    or VALIDATE (a validator checking the full document) — see
    build_prioritized_context's docstring for the exact rule.

    `iteration`/`validation_feedback` are for app/services/loop_engine.py's
    IMPROVE *loop step* — a revision pass, not a fresh draft, but NOT the
    same thing as the top-level IMPROVE action above: the loop's own
    self-correction isn't "a human explicitly asked to revise", so it does
    not by itself force full content. `iteration` only affects the mock
    path (see mock_agent.generate_mock_output's docstring);
    `validation_feedback` is included in a real call's context either way.
    """
    provider = get_active_provider()

    # Rule: a human-triggered revise (IMPROVE) or a validator's full-document
    # check (VALIDATE) both need full content, not a summary — see
    # build_prioritized_context's docstring.
    force_full_content = action in (AgentPromptRole.IMPROVE, AgentPromptRole.VALIDATE)

    budget_result = build_prioritized_context(
        project=project,
        node=node,
        approved_artifact_content=approved_artifact_content,
        approved_artifact_summaries=approved_artifact_summaries,
        freeform_context=freeform_context,
        context_token_budget=context_token_budget,
        output_token_budget=output_token_budget,
        full_content_artifact_types=full_content_artifact_types,
        force_full_content=force_full_content,
        retrieved_chunks=retrieved_chunks,
        validation_feedback=validation_feedback,
        review_comments=review_comments,
        current_draft_content=current_draft_content,
    )

    if provider == "mock":
        # Local import avoids a hard dependency the other direction (mock
        # generation has no reason to know about real generation).
        from app.services import mock_agent

        output_text = mock_agent.generate_mock_output(
            agent_key=active_prompt.agent_definition.agent_key,
            node=node,
            action=action,
            input_artifact_count=len(approved_artifact_content),
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
            estimated_context_tokens=budget_result.estimated_tokens,
            token_budget_report=budget_result.to_report_dict(),
        )

    system_prompt = _build_system_prompt(active_prompt)
    user_content = budget_result.assembled_text()

    if provider == "gemini":
        result = _generate_with_gemini(system_prompt, user_content, output_token_budget)
    elif provider == "nvidia":
        result = _generate_with_nvidia(system_prompt, user_content, output_token_budget)
    elif provider == "ollama":
        result = _generate_with_ollama(system_prompt, user_content, output_token_budget)
    else:
        result = _generate_with_anthropic(system_prompt, user_content, output_token_budget)

    result.estimated_context_tokens = budget_result.estimated_tokens
    result.token_budget_report = budget_result.to_report_dict()
    return result


def generate_raw_text(*, system_prompt: str, user_content: str, output_token_budget: int = 1024) -> str:
    """A minimal real-provider text completion, with none of `generate`'s
    two-part draft/clarification response contract — used by
    app/services/artifact_summary.py's real-AI summarization path (that
    module writes its own JSON-response instructions into `system_prompt`
    instead). Raises AIGenerationError on a provider failure, same as
    `generate`. Never called when the mock provider is active — mock
    summaries use a deterministic heuristic instead (see
    artifact_summary.py), so this assumes a real key is configured."""
    provider = get_active_provider()
    if provider == "gemini":
        result = _generate_with_gemini(system_prompt, user_content, output_token_budget)
    elif provider == "nvidia":
        result = _generate_with_nvidia(system_prompt, user_content, output_token_budget)
    elif provider == "ollama":
        result = _generate_with_ollama(system_prompt, user_content, output_token_budget)
    else:
        result = _generate_with_anthropic(system_prompt, user_content, output_token_budget)
    return result.content_markdown
