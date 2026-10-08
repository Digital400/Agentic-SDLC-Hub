"""Fits a very long freeform input (e.g. a large stakeholder request pasted
into Requirement Intake) into an agent run's context budget WITHOUT silently
cutting it off.

Why this exists: build_prioritized_context (ai_generation.py) puts every
freeform value into the P0 instruction block verbatim. A request longer than
the node's context budget therefore either crowds out the project's
engineering-setup rules and upstream artifacts, or is truncated with no
warning. Here, text over its share of the budget is instead condensed —
split at natural breaks, each part summarised by the active provider
(facts, numbers, dates, names and constraints preserved, filler dropped),
then merged — and an honest report of what was done is returned so the UI
can show it.

A provider failure on one part falls back to a deterministic extractive
summary for that part (never fails the whole run); the report counts these.
The mock provider always uses the extractive path, so this is fully
deterministic in tests and offline dev.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.token_budget import CHARS_PER_TOKEN, estimate_tokens

# Share of the node's context budget the freeform input may occupy before
# it is condensed; the rest is reserved for project rules, upstream
# artifacts and retrieved knowledge.
FREEFORM_BUDGET_SHARE = 0.5
DEFAULT_CONTEXT_TOKEN_BUDGET = 8000

# One provider call summarises at most this many characters of source text.
_PART_MAX_CHARS = 12_000
_MAX_ROUNDS = 3

_SUMMARISE_SYSTEM_PROMPT = (
    "You condense one part of a long requirements document so it fits a limited context window. "
    "Keep EVERY requirement, stakeholder name, number, date, deadline, budget, constraint, dependency, "
    "success metric and open question. Remove only repetition, greetings and filler. "
    "Never invent or infer facts that are not in the text. Where two statements contradict each other, "
    "keep both and mark them 'CONFLICT:'. Reply with the condensed text only, as concise bullet points, "
    "and stay within the requested length."
)

_KEYWORDS = re.compile(
    r"\b(must|shall|should|need|needs|require[sd]?|deadline|due|budget|constraint|cannot|can't|"
    r"not allowed|success|metric|kpi|stakeholder|owner|approve[sd]?|priority|risk|depend\w*|assum\w*|"
    r"out of scope|q[1-4]|\d{4}|\d+%|\$\s?\d)\b",
    re.IGNORECASE,
)


@dataclass
class CondensationReport:
    condensed: bool = False
    original_tokens: int = 0
    final_tokens: int = 0
    budget_tokens: int = 0
    fields: list[str] = field(default_factory=list)
    parts: int = 0
    rounds: int = 0
    provider_summaries: int = 0
    extractive_summaries: int = 0
    hard_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "condensed": self.condensed,
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "budget_tokens": self.budget_tokens,
            "fields": self.fields,
            "parts": self.parts,
            "rounds": self.rounds,
            "provider_summaries": self.provider_summaries,
            "extractive_summaries": self.extractive_summaries,
            "hard_truncated": self.hard_truncated,
        }


Summarizer = Callable[[str, int], tuple[str, bool]]
"""(text, target_tokens) -> (summary, used_provider)."""


def split_into_parts(text: str, max_chars: int = _PART_MAX_CHARS) -> list[str]:
    """Splits at blank lines / headings so a part never cuts a thought in
    half; an oversize paragraph is split at sentence ends, and as a last
    resort by length."""
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= max_chars:
            pieces.append(block)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", block)
        current = ""
        for sentence in sentences:
            while len(sentence) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            if len(current) + len(sentence) + 1 > max_chars and current:
                pieces.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)

    parts: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            parts.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        parts.append(current)
    return parts


def extractive_summary(text: str, target_tokens: int) -> str:
    """Deterministic fallback: keep the first sentence of every paragraph
    plus any sentence that carries a requirement signal (modal verbs,
    numbers, dates, budget, constraints...), in original order, up to the
    target length."""
    max_chars = max(200, target_tokens * CHARS_PER_TOKEN)
    lines: list[str] = []
    for paragraph in (p for p in re.split(r"\n\s*\n", text) if p.strip()):
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", paragraph) if s.strip()]
        for i, sentence in enumerate(sentences):
            if i == 0 or _KEYWORDS.search(sentence):
                lines.append(f"- {sentence.lstrip('-•* ').strip()}")
    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit("\n", 1)[0] + "\n- …(shortened further to fit)"
    return summary


def _provider_summarizer(text: str, target_tokens: int) -> tuple[str, bool]:
    if get_active_provider() == "mock":
        return extractive_summary(text, target_tokens), False
    try:
        summary = generate_raw_text(
            system_prompt=_SUMMARISE_SYSTEM_PROMPT,
            user_content=f"Condense to at most about {target_tokens} tokens:\n\n{text}",
            output_token_budget=max(256, target_tokens + 128),
        ).strip()
        if summary:
            return summary, True
    except AIGenerationError:
        pass
    return extractive_summary(text, target_tokens), False


def _condense_text(text: str, budget_tokens: int, summarize: Summarizer, report: CondensationReport) -> str:
    current = text
    for _ in range(_MAX_ROUNDS):
        if estimate_tokens(current) <= budget_tokens:
            return current
        parts = split_into_parts(current)
        report.rounds += 1
        report.parts += len(parts)
        per_part = max(64, budget_tokens // max(1, len(parts)))
        summaries = []
        for index, part in enumerate(parts, start=1):
            summary, used_provider = summarize(part, per_part)
            if used_provider:
                report.provider_summaries += 1
            else:
                report.extractive_summaries += 1
            summaries.append(f"[Part {index} of {len(parts)}]\n{summary}")
        merged = "\n\n".join(summaries)
        if merged == current:
            break
        current = merged
    if estimate_tokens(current) > budget_tokens:
        report.hard_truncated = True
        current = current[: budget_tokens * CHARS_PER_TOKEN].rstrip() + "\n…(truncated: input was far larger than the run's budget)"
    return current


def condense_freeform_context(
    freeform_context: dict[str, Any],
    *,
    context_token_budget: int | None,
    summarize: Summarizer | None = None,
) -> tuple[dict[str, Any], CondensationReport]:
    """Returns (context to actually use, report). The input dict is never
    mutated. Text within its budget share is returned unchanged
    (report.condensed False)."""
    summarize = summarize or _provider_summarizer
    budget = int((context_token_budget or DEFAULT_CONTEXT_TOKEN_BUDGET) * FREEFORM_BUDGET_SHARE)
    report = CondensationReport(budget_tokens=budget)

    text_keys = [k for k, v in freeform_context.items() if isinstance(v, str)]
    total = sum(estimate_tokens(freeform_context[k]) for k in text_keys)
    report.original_tokens = total
    report.final_tokens = total
    if total <= budget or not text_keys:
        return freeform_context, report

    # Give each text field a share of the budget proportional to its size,
    # so a short field (e.g. a mode selector) is never squeezed by a long one.
    result = dict(freeform_context)
    for key in text_keys:
        value = freeform_context[key]
        tokens = estimate_tokens(value)
        field_budget = max(64, int(budget * tokens / total))
        if tokens > field_budget:
            result[key] = _condense_text(value, field_budget, summarize, report)
            report.fields.append(key)

    report.condensed = True
    report.final_tokens = sum(estimate_tokens(result[k]) for k in text_keys)
    return result, report
