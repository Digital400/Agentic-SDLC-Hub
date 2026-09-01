"""ValidatorAgentService — an independent quality check on a drafted
artifact, run automatically by app/services/loop_engine.py's
LoopEngineService after every GENERATE_DRAFT/IMPROVE step (loop rule 4).

Distinct from AgentPromptRole.VALIDATE (a human-triggered top-level run
action where the drafting agent checks its own work) — this is a second,
independent agent, configured per workflow stage via
app/models/validator.py's ValidatorDefinition, that always runs as part
of the loop rather than on request. When a stage has no ValidatorDefinition
yet, `run_validator` falls back to a criteria-less heuristic check rather
than blocking the loop entirely.

Real AI when configured (see app/services/ai_generation.py's
get_active_provider) — one JSON-structured call via `generate_raw_text`,
independent of the drafting agent's own call. Falls back to a
deterministic heuristic otherwise, or if the real call errors or
misparses — matches app/services/artifact_summary.py's same resilience
pattern: a validation step must never crash the run over a formatting
slip or a provider outage.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider

if TYPE_CHECKING:
    from app.models import ValidatorDefinition

logger = logging.getLogger(__name__)

# Below this many words, a draft is too thin to score meaningfully at all
# — an automatic REJECT regardless of anything else.
CRITICAL_WORD_COUNT = 15
TARGET_WORD_COUNT = 120
_VALID_RECOMMENDATIONS = ("APPROVE", "REVISE", "REJECT")


@dataclass
class ValidatorResult:
    quality_score: float
    completeness_score: float
    clarity_score: float
    risk_coverage_score: float
    critical_issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    approval_recommendation: str = "REVISE"  # APPROVE | REVISE | REJECT
    # Specific gaps the validator found (e.g. "no Test Evidence section",
    # "Permission Rules doesn't name a role") — distinct from
    # critical_issues, which is "blocking problems" broadly; this is
    # specifically what's *missing* relative to what the stage should
    # cover. Added for the LLD validator (see
    # packages/prompts/validators/lld-validator.md) but available to every
    # stage since the schema is shared.
    missing_details: list[str] = field(default_factory=list)
    # Specific risks the validator identified in the draft's own content —
    # distinct from risk_coverage_score, which only scores *whether* risks
    # were called out at all, not what they are.
    risks: list[str] = field(default_factory=list)

    @property
    def has_critical_issues(self) -> bool:
        return bool(self.critical_issues)

    @property
    def recommendation(self) -> str:
        """A coarser, two-value reviewer-facing recommendation, derived
        from approval_recommendation — see the LLD validator's contract.
        APPROVE -> READY_FOR_REVIEW; REVISE/REJECT both collapse to
        NEEDS_IMPROVEMENT (a human reviewer doesn't need a third state to
        decide whether to look at it yet)."""
        return "READY_FOR_REVIEW" if self.approval_recommendation == "APPROVE" else "NEEDS_IMPROVEMENT"

    def to_dict(self) -> dict:
        return {
            "quality_score": self.quality_score,
            "completeness_score": self.completeness_score,
            "clarity_score": self.clarity_score,
            "risk_coverage_score": self.risk_coverage_score,
            "critical_issues": self.critical_issues,
            "suggestions": self.suggestions,
            "approval_recommendation": self.approval_recommendation,
            "missing_details": self.missing_details,
            "risks": self.risks,
            "recommendation": self.recommendation,
        }


# --- Mock / fallback path: plain heuristics, no model call --------------------------

_RISK_KEYWORDS = ("risk", "mitigation", "constraint", "assumption", "limitation", "caveat", "concern", "dependency")


def _criteria_coverage(content_lower: str, criteria: list[str]) -> tuple[float, list[str]]:
    """A criterion counts as covered if at least half its significant
    (>3 char) words appear in the content — a loose keyword match, not
    semantic understanding; matches this codebase's other heuristic
    scorers (see app/services/artifact_summary.py)."""
    if not criteria:
        return 1.0, []
    missing = []
    for item in criteria:
        significant_words = [w.strip(".,:;()").lower() for w in item.split() if len(w) > 3]
        covered = not significant_words or (
            sum(1 for w in significant_words if w in content_lower) / len(significant_words) >= 0.5
        )
        if not covered:
            missing.append(item)
    return (len(criteria) - len(missing)) / len(criteria), missing


def _risk_coverage_score(content_lower: str) -> float:
    """Does the document even mention risk-shaped language at all? Not a
    judgment of whether the *right* risks were identified — that needs
    real judgment, which is what the real-AI path is for."""
    hits = sum(1 for kw in _RISK_KEYWORDS if kw in content_lower)
    return min(1.0, hits / 2)  # two distinct mentions already counts as "covered"


def _clarity_score(content_markdown: str) -> float:
    """A structural proxy for clarity (has headings, no single
    wall-of-words line) — not real readability analysis."""
    lines = [line for line in content_markdown.splitlines() if line.strip()]
    if not lines:
        return 0.0
    score = 0.5
    if any(line.strip().startswith("#") for line in lines):
        score += 0.3
    if max(len(line.split()) for line in lines) <= 80:
        score += 0.2
    return min(1.0, score)


def _run_heuristic_validator(*, content_markdown: str, criteria: list[str]) -> ValidatorResult:
    stripped = content_markdown.strip()
    word_count = len(stripped.split())

    if not stripped or word_count < CRITICAL_WORD_COUNT:
        return ValidatorResult(
            quality_score=0.0,
            completeness_score=0.0,
            clarity_score=0.0,
            risk_coverage_score=0.0,
            critical_issues=[f"Draft has only {word_count} word(s) — far too little content to review."],
            suggestions=["Expand the draft to cover the stage's required content before re-validating."],
            approval_recommendation="REJECT",
            missing_details=list(criteria),  # nothing this short could have covered any of them
        )

    content_lower = stripped.lower()
    length_factor = min(1.0, word_count / TARGET_WORD_COUNT)
    criteria_coverage, missing_criteria = _criteria_coverage(content_lower, criteria)

    completeness_score = round(0.4 * length_factor + 0.6 * criteria_coverage, 3)
    clarity_score = round(_clarity_score(content_markdown), 3)
    risk_coverage_score = round(_risk_coverage_score(content_lower), 3)
    quality_score = round((completeness_score + clarity_score + risk_coverage_score) / 3, 3)

    critical_issues: list[str] = []
    suggestions: list[str] = []

    if length_factor < 0.3:
        critical_issues.append(f"Draft is much shorter than expected ({word_count} words).")
        suggestions.append("Add more detail — the draft is far short of the expected length for this stage.")
    elif length_factor < 0.6:
        suggestions.append(f"Consider expanding the draft ({word_count} words) for fuller coverage.")

    missing_fraction = len(missing_criteria) / len(criteria) if criteria else 0.0
    for item in missing_criteria:
        if missing_fraction > 0.5:
            critical_issues.append(f"Criterion not clearly addressed: '{item}'.")
        suggestions.append(f"Address: {item}")

    if risk_coverage_score < 0.5:
        suggestions.append("Call out relevant risks, constraints, or assumptions explicitly.")
    if clarity_score < 0.7:
        suggestions.append("Improve structure — use headings and keep individual points concise.")

    if critical_issues:
        approval_recommendation = "REJECT" if quality_score < 0.3 else "REVISE"
    elif quality_score < 0.8:
        approval_recommendation = "REVISE"
    else:
        approval_recommendation = "APPROVE"

    return ValidatorResult(
        quality_score=quality_score,
        completeness_score=completeness_score,
        clarity_score=clarity_score,
        risk_coverage_score=risk_coverage_score,
        critical_issues=critical_issues,
        suggestions=suggestions,
        approval_recommendation=approval_recommendation,
        missing_details=missing_criteria,
        # The heuristic only scores *whether* risk-shaped language appears
        # (risk_coverage_score) — it has no real judgment to enumerate
        # specific risks, unlike the real-AI path. Left empty rather than
        # guessed.
        risks=[],
    )


# --- Real-AI path ---------------------------------------------------------------------

_VALIDATOR_SYSTEM_PROMPT = (
    "You are an independent validator reviewing a drafted SDLC stage artifact — you did not "
    "write it, and your job is to score it honestly, not to rubber-stamp it.\n"
    "Respond with ONLY a single JSON object (no markdown code fences, no commentary) with "
    "exactly these keys:\n"
    '- "quality_score": float 0.0-1.0, your overall judgment.\n'
    '- "completeness_score": float 0.0-1.0 — does it cover everything this stage requires?\n'
    '- "clarity_score": float 0.0-1.0 — is it well-structured, unambiguous, and technically precise '
    "enough for a developer to act on directly?\n"
    '- "risk_coverage_score": float 0.0-1.0 — does it call out relevant risks, constraints, or '
    "assumptions where they matter?\n"
    '- "critical_issues": array of strings — blocking problems; empty array if none.\n'
    '- "suggestions": array of strings — concrete, actionable improvements.\n'
    '- "missing_details": array of strings — specific required content that is absent or too thin '
    "(e.g. a required section that's missing, or present but empty); empty array if nothing is missing.\n"
    '- "risks": array of strings — the SPECIFIC risks or assumptions the draft itself identifies (not '
    "whether it identifies any — list what they actually are); empty array if none are stated.\n"
    '- "approval_recommendation": one of "APPROVE", "REVISE", "REJECT".\n'
    "Only list something under critical_issues if it would genuinely block using this artifact "
    "as-is — do not pad the list."
)


def _run_real_validator(*, stage_name: str, criteria: list[str], content_markdown: str) -> ValidatorResult:
    criteria_text = "\n".join(f"- {c}" for c in criteria) or "(no stage-specific criteria configured)"
    user_content = (
        f"# Stage: {stage_name}\n\n"
        f"## Validation criteria for this stage\n{criteria_text}\n\n"
        f"## Draft to validate\n{content_markdown}"
    )
    raw = generate_raw_text(
        system_prompt=_VALIDATOR_SYSTEM_PROMPT, user_content=user_content, output_token_budget=1024
    )
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    parsed = json.loads(cleaned)

    recommendation = str(parsed.get("approval_recommendation", "REVISE")).upper()
    if recommendation not in _VALID_RECOMMENDATIONS:
        recommendation = "REVISE"

    return ValidatorResult(
        quality_score=float(parsed.get("quality_score", 0.0)),
        completeness_score=float(parsed.get("completeness_score", 0.0)),
        clarity_score=float(parsed.get("clarity_score", 0.0)),
        risk_coverage_score=float(parsed.get("risk_coverage_score", 0.0)),
        critical_issues=[str(x) for x in parsed.get("critical_issues", [])],
        suggestions=[str(x) for x in parsed.get("suggestions", [])],
        approval_recommendation=recommendation,
        missing_details=[str(x) for x in parsed.get("missing_details", [])],
        risks=[str(x) for x in parsed.get("risks", [])],
    )


# --- Entry point -----------------------------------------------------------------------


def run_validator(
    *, validator: "ValidatorDefinition | None", stage_name: str, content_markdown: str
) -> ValidatorResult:
    """The single entry point — app/services/loop_engine.py calls this
    after every GENERATE_DRAFT/IMPROVE step. `validator` may be None when
    no ValidatorDefinition exists yet for this stage (see
    app/db/seed.py's _ensure_validator_definitions for how the default
    workflow gets one per stage) — falls back to a criteria-less heuristic
    check rather than blocking the loop entirely.

    Real AI when configured; the deterministic heuristic otherwise, or if
    the real call errors or returns unparseable JSON (logged, not raised —
    a validation step must never fail the run over a bad response)."""
    criteria = validator.criteria if validator else []

    if get_active_provider() == "mock":
        return _run_heuristic_validator(content_markdown=content_markdown, criteria=criteria)

    try:
        return _run_real_validator(stage_name=stage_name, criteria=criteria, content_markdown=content_markdown)
    except (AIGenerationError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Real-AI validator failed (%s); falling back to heuristic validation.", exc)
        return _run_heuristic_validator(content_markdown=content_markdown, criteria=criteria)
