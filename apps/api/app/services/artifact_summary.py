"""ArtifactSummaryService — the compression layer between an approved
artifact's full content and what most later stages actually need to see.

Called once per approved artifact version (see
app/api/routes/reviews.py's approve_review) to populate
ArtifactVersion's executiveSummary / agentContextSummary / keyDecisions /
openQuestions / risks / generatedSummaryAt — see app/models/artifact.py.
`agentContextSummary` is what app/services/ai_generation.py's
build_prioritized_context uses by default instead of fullContent.

Uses a real model call when one is configured (see
app/services/ai_generation.py's get_active_provider) — one lightweight,
JSON-structured request per approved version, reusing `generate_raw_text`
rather than the full draft/clarification pipeline `generate` runs.
Falls back to a deterministic heuristic extraction (matches this
codebase's mock-first convention — see mock_agent.py) when no provider is
configured, or if the real call's response isn't valid JSON: an approval
should never be blocked by the summarizer misbehaving.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider

logger = logging.getLogger(__name__)

EXECUTIVE_SUMMARY_MAX_CHARS = 400
AGENT_CONTEXT_SUMMARY_MAX_CHARS = 1200
_MAX_EXTRACTED_ITEMS = 5


@dataclass
class ArtifactSummaryResult:
    executive_summary: str
    agent_context_summary: str
    key_decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


# --- Mock / fallback path: plain heuristics, no model call --------------------------

# A line matching one of these is a *candidate* for that bucket — a simple
# keyword scan, not NLP. Good enough for a first version: it only ever
# surfaces things the document already states in recognizable language,
# never invents anything (the real-AI prompt below carries the same rule).
_KEYWORD_PATTERNS = {
    "key_decisions": re.compile(r"\b(decision|decided|we will|chosen|selected)\b", re.IGNORECASE),
    "open_questions": re.compile(r"\?|(open question|tbd|to be determined|needs clarification)", re.IGNORECASE),
    "risks": re.compile(r"\b(risk|concern|caveat|caution|mitigation)\b", re.IGNORECASE),
}


def _extract_lines(content_markdown: str, pattern: re.Pattern, max_items: int = _MAX_EXTRACTED_ITEMS) -> list[str]:
    items = []
    for line in content_markdown.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        if stripped and pattern.search(stripped):
            items.append(stripped)
        if len(items) >= max_items:
            break
    return items


def _first_paragraph(content_markdown: str) -> str:
    """The first paragraph of actual prose — skips markdown heading-only
    paragraphs (e.g. "# Problem Statement" on its own line), since those
    aren't a summary of anything, just a section title. Falls back to the
    heading text itself only if the document truly has nothing else."""
    fallback = ""
    for para in content_markdown.split("\n\n"):
        stripped = para.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if not fallback:
                fallback = stripped.lstrip("#").strip()
            continue
        return stripped
    return fallback or content_markdown.strip()


def _generate_heuristic_summaries(content_markdown: str) -> ArtifactSummaryResult:
    executive_summary = _first_paragraph(content_markdown)[:EXECUTIVE_SUMMARY_MAX_CHARS] or "(No content to summarize.)"

    agent_context_summary = content_markdown.strip()[:AGENT_CONTEXT_SUMMARY_MAX_CHARS]
    if len(content_markdown.strip()) > AGENT_CONTEXT_SUMMARY_MAX_CHARS:
        agent_context_summary += "\n…(condensed — see fullContent for the complete document)"
    agent_context_summary = agent_context_summary or "(No content to summarize.)"

    return ArtifactSummaryResult(
        executive_summary=executive_summary,
        agent_context_summary=agent_context_summary,
        key_decisions=_extract_lines(content_markdown, _KEYWORD_PATTERNS["key_decisions"]),
        open_questions=_extract_lines(content_markdown, _KEYWORD_PATTERNS["open_questions"]),
        risks=_extract_lines(content_markdown, _KEYWORD_PATTERNS["risks"]),
    )


# --- Real-AI path ---------------------------------------------------------------------

_SUMMARY_SYSTEM_PROMPT = (
    "You compress SDLC artifacts for two audiences: a human skimming for the gist, and another "
    "AI agent that needs enough context to build on this artifact without reading it in full. "
    "Respond with ONLY a single JSON object (no markdown code fences, no commentary) with exactly "
    "these keys:\n"
    '- "executive_summary": 2-3 sentences, for a human.\n'
    '- "agent_context_summary": a denser paragraph or two capturing the specifics — decisions, '
    "scope, constraints — another agent would need to produce correct downstream work.\n"
    '- "key_decisions": array of strings — decisions explicitly made in the document.\n'
    '- "open_questions": array of strings — explicitly unresolved questions.\n'
    '- "risks": array of strings — explicitly called-out risks or concerns.\n'
    "Use an empty array for any of the last three the document doesn't actually contain — never "
    "invent one."
)


def _generate_real_summaries(*, artifact_type: str, content_markdown: str) -> ArtifactSummaryResult:
    raw = generate_raw_text(
        system_prompt=_SUMMARY_SYSTEM_PROMPT,
        user_content=f"# {artifact_type}\n\n{content_markdown}",
        output_token_budget=1024,
    )
    # Models occasionally wrap JSON in a code fence despite being told not
    # to — strip it rather than fail the whole summary over formatting.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    parsed = json.loads(cleaned)

    return ArtifactSummaryResult(
        executive_summary=str(parsed.get("executive_summary", "")).strip()
        or _first_paragraph(content_markdown)[:EXECUTIVE_SUMMARY_MAX_CHARS],
        agent_context_summary=str(parsed.get("agent_context_summary", "")).strip()
        or content_markdown[:AGENT_CONTEXT_SUMMARY_MAX_CHARS],
        key_decisions=[str(x) for x in parsed.get("key_decisions", [])],
        open_questions=[str(x) for x in parsed.get("open_questions", [])],
        risks=[str(x) for x in parsed.get("risks", [])],
    )


# --- Entry point -----------------------------------------------------------------------


def generate_artifact_summaries(*, artifact_type: str, content_markdown: str) -> ArtifactSummaryResult:
    """The single entry point — approve_review calls this for every
    approved artifact version. Real AI when configured; the deterministic
    heuristic otherwise, or if the real call errors or returns unparseable
    JSON (logged, not raised — an approval should never fail because the
    summarizer had a bad response).
    
    OPTIMIZATION: Always use fast heuristic summaries when Ollama is the
    active provider, to avoid slow CPU-based generation on approval.
    Ollama is still used for agent runs (which are expected to take time),
    but approval should be instant."""
    if not content_markdown.strip():
        return ArtifactSummaryResult(executive_summary="(Empty artifact.)", agent_context_summary="(Empty artifact.)")

    provider = get_active_provider()
    # Use fast heuristics for mock OR ollama (CPU inference is too slow for approval)
    if provider in ("mock", "ollama"):
        return _generate_heuristic_summaries(content_markdown)

    try:
        return _generate_real_summaries(artifact_type=artifact_type, content_markdown=content_markdown)
    except (AIGenerationError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        logger.warning("Real-AI artifact summarization failed (%s); falling back to heuristic extraction.", exc)
        return _generate_heuristic_summaries(content_markdown)


def apply_summaries_to_version(version, *, artifact_type: str) -> None:
    """Generates summaries for `version` (an ArtifactVersion) and writes
    them onto it in place, including generated_summary_at — the caller
    (approve_review) is responsible for adding it to the session/committing;
    this function only sets attributes, matching this codebase's other
    "apply to an ORM object" service helpers (e.g. GraphEngineService's
    mark_* methods)."""
    result = generate_artifact_summaries(artifact_type=artifact_type, content_markdown=version.content_markdown)
    version.executive_summary = result.executive_summary
    version.agent_context_summary = result.agent_context_summary
    version.key_decisions = result.key_decisions
    version.open_questions = result.open_questions
    version.risks = result.risks
    version.generated_summary_at = datetime.now(timezone.utc)
