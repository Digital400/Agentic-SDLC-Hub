"""Phase 18: evaluation harness — see app/services/runtime_evaluation.py.
"""

import json

from app.services.runtime_evaluation import (
    EvaluationCase,
    RunnerOutput,
    load_golden_cases,
    run_evaluation,
)


def _output(**overrides) -> RunnerOutput:
    defaults = dict(
        content="draft", schema_valid=True, required_sections_present=["Objective", "Scope"],
        needed_clarification=False, clarification_quality_score=None, validation_findings=[],
        latency_seconds=1.0, total_tokens=100, actual_cost_usd=0.01, cache_savings_usd=0.0,
        tool_failures=0, permission_violations=0, patch_accepted=None, tests_and_ci_passed=None,
        pr_review_useful=None,
    )
    defaults.update(overrides)
    return RunnerOutput(**defaults)


def _cases(n: int) -> list[EvaluationCase]:
    return [EvaluationCase(case_id=f"case-{i}", task_type="REQUIREMENT_ANALYSIS", input_context={}) for i in range(n)]


class TestRunEvaluation:
    def test_runs_both_runners_against_every_case(self):
        calls = {"legacy": 0, "new": 0}

        def legacy_runner(case):
            calls["legacy"] += 1
            return _output()

        def new_runner(case):
            calls["new"] += 1
            return _output()

        run_evaluation(_cases(3), legacy_runner, new_runner)
        assert calls == {"legacy": 3, "new": 3}

    def test_schema_validity_rate_reflects_actual_pass_count(self):
        outputs = iter([_output(schema_valid=True), _output(schema_valid=False)])
        report = run_evaluation(_cases(2), lambda c: next(outputs), lambda c: _output(schema_valid=True))
        assert report.schema_validity_rate("legacy") == 0.5
        assert report.schema_validity_rate("new") == 1.0

    def test_required_section_completeness_rate(self):
        report = run_evaluation(
            _cases(1),
            lambda c: _output(required_sections_present=["Objective"]),
            lambda c: _output(required_sections_present=["Objective", "Scope"]),
        )
        assert report.required_section_completeness_rate("legacy", ["Objective", "Scope"]) == 0.5
        assert report.required_section_completeness_rate("new", ["Objective", "Scope"]) == 1.0

    def test_clarification_rate(self):
        report = run_evaluation(_cases(2), lambda c: _output(needed_clarification=True), lambda c: _output(needed_clarification=False))
        assert report.clarification_rate("legacy") == 1.0
        assert report.clarification_rate("new") == 0.0

    def test_totals_sum_across_every_case(self):
        report = run_evaluation(_cases(2), lambda c: _output(total_tokens=100, actual_cost_usd=0.5), lambda c: _output(total_tokens=50, actual_cost_usd=0.1))
        assert report.total_tokens("legacy") == 200
        assert report.total_cost_usd("legacy") == 1.0
        assert report.total_tokens("new") == 100
        assert report.total_cost_usd("new") == 0.2

    def test_patch_acceptance_rate_is_none_when_no_case_is_patch_relevant(self):
        report = run_evaluation(_cases(1), lambda c: _output(patch_accepted=None), lambda c: _output(patch_accepted=None))
        assert report.patch_acceptance_rate("legacy") is None

    def test_patch_acceptance_rate_ignores_non_applicable_cases(self):
        outputs = iter([_output(patch_accepted=True), _output(patch_accepted=None)])
        report = run_evaluation(_cases(2), lambda c: next(outputs), lambda c: _output(patch_accepted=False))
        assert report.patch_acceptance_rate("legacy") == 1.0
        assert report.patch_acceptance_rate("new") == 0.0

    def test_human_acceptance_without_edits_rate_is_none_until_a_human_records_one(self):
        report = run_evaluation(_cases(1), lambda c: _output(), lambda c: _output())
        assert report.human_acceptance_without_edits_rate("legacy") is None
        assert report.human_acceptance_without_edits_rate("new") is None

    def test_select_rejects_an_invalid_which_argument(self):
        report = run_evaluation(_cases(1), lambda c: _output(), lambda c: _output())
        try:
            report.schema_validity_rate("both")
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestLoadGoldenCases:
    def test_returns_an_empty_list_for_a_missing_directory(self, tmp_path):
        assert load_golden_cases(tmp_path / "does-not-exist") == []

    def test_returns_an_empty_list_for_the_real_shipped_golden_directory(self):
        # The real .sdlc/evaluation/golden/ ships with no cases by design
        # (see its own README.md) — this asserts that disclosed state,
        # not a bug.
        assert load_golden_cases() == []

    def test_loads_a_real_case_file(self, tmp_path):
        case_file = tmp_path / "case1.json"
        case_file.write_text(json.dumps({
            "case_id": "case-1", "task_type": "REQUIREMENT_ANALYSIS",
            "input_context": {"business_objective": "Grow revenue"},
        }), encoding="utf-8")
        cases = load_golden_cases(tmp_path)
        assert len(cases) == 1
        assert cases[0].case_id == "case-1"
        assert cases[0].input_context["business_objective"] == "Grow revenue"
