"""Phase 18: "Build an evaluation command using the golden dataset and
redacted historical approved examples. Compare legacy and new runtimes
on [14 named metrics]."

This module is a real, runnable comparison harness — it takes a list of
`EvaluationCase` (a WorkPacket-shaped input plus, optionally, a known-good
reference output) and two callables (`legacy_runner`, `new_runner`), runs
both against every case, and reports the 14 named metrics side by side.

**No golden dataset or redacted historical example ships with this
phase.** `.sdlc/evaluation/golden/` is a real, documented directory this
module reads from (`load_golden_cases`), currently empty. Populating it
with real redacted historical approved examples requires a real
redaction/anonymization pipeline over real historical data this
environment does not have access to — inventing synthetic "historical"
examples and presenting them as real would be exactly the kind of
fabrication this migration's own hard rules forbid. The harness itself
is complete and tested against synthetic, clearly-fixture-labeled cases;
running it against a real golden dataset is a follow-up once one is
curated by a human with access to real (redacted) production history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_DATASET_DIR = REPO_ROOT / ".sdlc" / "evaluation" / "golden"


@dataclass
class EvaluationCase:
    case_id: str
    task_type: str
    input_context: dict[str, str]
    reference_output: str | None = None  # a known-good prior human-approved output, when available.


@dataclass
class RunnerOutput:
    """What one runner (legacy or new) produced for one case — the raw
    material every metric below is computed from. A runner is expected to
    report these honestly (e.g. tool_failures=0 really means zero
    failures, not 'not measured')."""

    content: str
    schema_valid: bool
    required_sections_present: list[str]
    needed_clarification: bool
    clarification_quality_score: float | None  # 0-1, None if not applicable (no clarification requested).
    validation_findings: list[str]
    latency_seconds: float
    total_tokens: int
    actual_cost_usd: float
    cache_savings_usd: float
    tool_failures: int
    permission_violations: int
    patch_accepted: bool | None  # None for a document-only case.
    tests_and_ci_passed: bool | None
    pr_review_useful: bool | None
    human_accepted_without_edits: bool | None = None  # filled in later by a human reviewer, never inferred.


@dataclass
class CaseComparison:
    case_id: str
    legacy: RunnerOutput
    new: RunnerOutput


@dataclass
class EvaluationReport:
    cases: list[CaseComparison] = field(default_factory=list)

    def schema_validity_rate(self, which: str) -> float:
        outputs = self._select(which)
        return sum(1 for o in outputs if o.schema_valid) / len(outputs) if outputs else 0.0

    def required_section_completeness_rate(self, which: str, expected_sections: list[str]) -> float:
        outputs = self._select(which)
        if not outputs or not expected_sections:
            return 0.0
        scores = [len(set(o.required_sections_present) & set(expected_sections)) / len(expected_sections) for o in outputs]
        return sum(scores) / len(scores)

    def clarification_rate(self, which: str) -> float:
        outputs = self._select(which)
        return sum(1 for o in outputs if o.needed_clarification) / len(outputs) if outputs else 0.0

    def average_latency_seconds(self, which: str) -> float:
        outputs = self._select(which)
        return sum(o.latency_seconds for o in outputs) / len(outputs) if outputs else 0.0

    def total_tokens(self, which: str) -> int:
        return sum(o.total_tokens for o in self._select(which))

    def total_cost_usd(self, which: str) -> float:
        return sum(o.actual_cost_usd for o in self._select(which))

    def total_cache_savings_usd(self, which: str) -> float:
        return sum(o.cache_savings_usd for o in self._select(which))

    def total_tool_failures(self, which: str) -> int:
        return sum(o.tool_failures for o in self._select(which))

    def total_permission_violations(self, which: str) -> int:
        return sum(o.permission_violations for o in self._select(which))

    def patch_acceptance_rate(self, which: str) -> float | None:
        relevant = [o.patch_accepted for o in self._select(which) if o.patch_accepted is not None]
        return (sum(1 for a in relevant if a) / len(relevant)) if relevant else None

    def test_and_ci_success_rate(self, which: str) -> float | None:
        relevant = [o.tests_and_ci_passed for o in self._select(which) if o.tests_and_ci_passed is not None]
        return (sum(1 for a in relevant if a) / len(relevant)) if relevant else None

    def human_acceptance_without_edits_rate(self, which: str) -> float | None:
        """None until at least one case has a real, human-recorded
        judgement — never fabricated or defaulted to a number."""
        relevant = [o.human_accepted_without_edits for o in self._select(which) if o.human_accepted_without_edits is not None]
        return (sum(1 for a in relevant if a) / len(relevant)) if relevant else None

    def _select(self, which: str) -> list[RunnerOutput]:
        if which not in ("legacy", "new"):
            raise ValueError(f"which must be 'legacy' or 'new', got {which!r}")
        return [getattr(c, which) for c in self.cases]


def run_evaluation(
    cases: list[EvaluationCase],
    legacy_runner: Callable[[EvaluationCase], RunnerOutput],
    new_runner: Callable[[EvaluationCase], RunnerOutput],
) -> EvaluationReport:
    comparisons = [CaseComparison(case_id=c.case_id, legacy=legacy_runner(c), new=new_runner(c)) for c in cases]
    return EvaluationReport(cases=comparisons)


def load_golden_cases(directory: Path = GOLDEN_DATASET_DIR) -> list[EvaluationCase]:
    """Loads every *.json file in `directory` as one EvaluationCase.
    Returns an empty list (never an error) for a missing or empty
    directory — "no golden dataset curated yet" is a valid, common state,
    not a failure."""
    if not directory.is_dir():
        return []
    cases = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(EvaluationCase(
            case_id=data["case_id"], task_type=data["task_type"], input_context=data.get("input_context", {}),
            reference_output=data.get("reference_output"),
        ))
    return cases
