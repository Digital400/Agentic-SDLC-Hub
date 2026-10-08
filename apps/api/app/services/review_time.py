"""The "a story's PR must be reviewable by one human" rule.

Story Crafting's prompt (see app/db/seed.py's story_crafting rule 7) asks the
agent to size every story so its pull request can realistically be reviewed
by one person, and to state that as three numbers of minutes — best /
typical / worst case, e.g. "5 / 15 / 30" — with the worst case capped at 30.
This module is the one place that parses that free-text field and checks the
cap, so the check behaves identically wherever a story backlog is validated:
the in-app Story Crafting Validate action's checklist (prose only, not
enforced numerically there), the coding-tool skill's local document checker,
and the sync-back that pulls a repository-written backlog into the app.
"""

import re
from dataclasses import dataclass

MAX_WORST_CASE_MINUTES = 30
DEFAULT_TYPICAL_MINUTES = 15
DEFAULT_BEST_CASE_MINUTES = 5

# "5 / 15 / 30", "5/15/30", "5, 15, 30", "5-15-30", optionally with a
# trailing "(pair recommended ...)" note the prompt allows.
_THREE_NUMBERS_RE = re.compile(r"(\d+)\s*(?:/|,|-|to)\s*(\d+)\s*(?:/|,|-|to)\s*(\d+)")
_ONE_NUMBER_RE = re.compile(r"(\d+)")


@dataclass
class ReviewTimeEstimate:
    best_case_minutes: int | None
    typical_minutes: int | None
    worst_case_minutes: int | None
    pair_recommended: bool
    raw: str

    @property
    def exceeds_budget(self) -> bool:
        return self.worst_case_minutes is not None and self.worst_case_minutes > MAX_WORST_CASE_MINUTES

    @property
    def is_stated(self) -> bool:
        return self.raw.strip() != ""


def parse_review_time(raw: str | None) -> ReviewTimeEstimate:
    """Never raises. An unparseable or empty value comes back with all three
    numbers None — `is_stated`/`exceeds_budget` distinguish "not given" from
    "given and over budget" for callers that need to tell those apart."""
    raw = (raw or "").strip()
    pair_recommended = "pair" in raw.lower()
    match = _THREE_NUMBERS_RE.search(raw)
    if match:
        best, typical, worst = (int(g) for g in match.groups())
        return ReviewTimeEstimate(best, typical, worst, pair_recommended, raw)
    single = _ONE_NUMBER_RE.search(raw)
    if single:
        # A story that only gave one number — treat it as the worst case
        # (the one the 30-minute cap actually gates), not silently ignored.
        worst = int(single.group(1))
        return ReviewTimeEstimate(None, None, worst, pair_recommended, raw)
    return ReviewTimeEstimate(None, None, None, pair_recommended, raw)


def review_time_problems(title: str, raw: str | None) -> list[str]:
    """Human-readable problems for one story's Estimated PR Review Time —
    used by both stage_document_sync.py's story-backlog check and the
    generated Node.js validator's story-backlog branch (kept in sync by
    hand; the JS one is a simplified mirror, same relationship the rest of
    coding_tool_skills.py's validator already has to its Python counterpart)."""
    estimate = parse_review_time(raw)
    if not estimate.is_stated:
        return [f'"{title}": missing Estimated PR Review Time']
    if estimate.worst_case_minutes is None:
        return [f'"{title}": Estimated PR Review Time ("{estimate.raw}") is not a recognizable minutes estimate']
    if estimate.exceeds_budget:
        return [
            f'"{title}": Estimated PR Review Time worst case is {estimate.worst_case_minutes} minutes, over the '
            f"{MAX_WORST_CASE_MINUTES}-minute limit — split this story into smaller, independently reviewable stories"
        ]
    return []
