"""Deterministic quality scoring for a drafted artifact — the "VALIDATE"
step of app/services/loop_engine.py's LoopEngineService.

There's no real-AI structured-output path for "score this draft 0-1 and
list its issues" today (see app/services/ai_generation.py's two-part
response format, which only covers needs_clarification) — building one
would mean trusting a model's own self-report of its quality, which is
exactly the kind of grading a validation step shouldn't take on faith.
This heuristic scorer is intentionally provider-independent — it runs the
same way whether the draft came from the real Claude/Gemini call or the
mock generator, so the loop's stop conditions are exercised deterministically
either way (see loop_engine.py's docstring on why mock generation must
grow between iterations for the loop's IMPROVE step to be observable).
"""

from dataclasses import dataclass, field

# Below this many words, a draft is considered too thin to review
# regardless of anything else — an empty or single-sentence "draft" is a
# critical issue on its own, not just a minor length ding.
CRITICAL_WORD_COUNT = 15
# Word count at or above which length stops being a scoring factor at all.
TARGET_WORD_COUNT = 120


@dataclass
class ValidationIssue:
    severity: str  # "critical" | "minor"
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "message": self.message}


@dataclass
class ValidationResult:
    quality_score: float  # 0.0-1.0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def has_critical_issues(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    def issues_as_dicts(self) -> list[dict]:
        return [i.to_dict() for i in self.issues]


def _checklist_item_present(item: str, content_lower: str) -> bool:
    """A loose match: the checklist item's own words appearing in the
    draft, not an exact phrase — checklist items are written as guidance
    ("Includes a rollback plan"), not as required literal strings."""
    significant_words = [w.strip(".,:;()").lower() for w in item.split() if len(w) > 3]
    if not significant_words:
        return True
    hits = sum(1 for w in significant_words if w in content_lower)
    return hits / len(significant_words) >= 0.5


def validate_draft_quality(*, content_markdown: str, checklist: list[str]) -> ValidationResult:
    """Scores a draft on two factors: is it substantial enough (length),
    and does it appear to cover the agent's own checklist (keyword-ish
    presence). Weighted 40/60 — checklist coverage matters more than raw
    length, but a checklist-covering one-liner still isn't a real draft.
    """
    stripped = content_markdown.strip()
    word_count = len(stripped.split())

    if not stripped or word_count < CRITICAL_WORD_COUNT:
        return ValidationResult(
            quality_score=0.0,
            issues=[
                ValidationIssue(
                    "critical",
                    f"Draft has only {word_count} word(s) — far too little content to review.",
                )
            ],
        )

    issues: list[ValidationIssue] = []
    length_factor = min(1.0, word_count / TARGET_WORD_COUNT)
    if length_factor < 0.6:
        issues.append(
            ValidationIssue(
                "critical" if length_factor < 0.3 else "minor",
                f"Draft is shorter than expected ({word_count} words) — likely missing coverage.",
            )
        )

    content_lower = stripped.lower()
    if checklist:
        missing = [item for item in checklist if not _checklist_item_present(item, content_lower)]
        checklist_factor = (len(checklist) - len(missing)) / len(checklist)
        missing_fraction = len(missing) / len(checklist)
        for item in missing:
            issues.append(
                ValidationIssue(
                    "critical" if missing_fraction > 0.5 else "minor",
                    f"Checklist item not clearly addressed: '{item}'.",
                )
            )
    else:
        checklist_factor = 1.0

    quality_score = round(0.4 * length_factor + 0.6 * checklist_factor, 3)
    return ValidationResult(quality_score=quality_score, issues=issues)
