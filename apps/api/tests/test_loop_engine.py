"""Unit tests for LoopEngineService — see app/services/loop_engine.py.

`generate` and `run_validator` are monkeypatched so each test controls
exactly what a "model call" and a "validator agent pass" return,
independent of the mock/real AI provider and independent of
validator_agent.py's actual scoring heuristic — what's under test here is
the loop's own control flow (rules 4 and 5), not content generation or
quality scoring, which have their own concerns elsewhere.
"""

from app.models import AgentPromptRole, LoopStatus, LoopStepType, WorkflowStatus
from app.services.ai_generation import AgentGenerationResult
from app.services.loop_engine import LoopEngineService
from app.services.validator_agent import ValidatorResult
from tests.conftest import make_agent_prompt, make_agent_run, make_node


def _draft_result(content: str = "draft content", needs_clarification: bool = False) -> AgentGenerationResult:
    return AgentGenerationResult(
        content_markdown=content,
        needs_clarification=needs_clarification,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        cost=0.01,
        used_mock=True,
    )


def _validation(
    quality_score: float, critical_issues: list[str] | None = None, suggestions: list[str] | None = None
) -> ValidatorResult:
    return ValidatorResult(
        quality_score=quality_score,
        completeness_score=quality_score,
        clarity_score=quality_score,
        risk_coverage_score=quality_score,
        critical_issues=critical_issues or [],
        suggestions=suggestions or [],
        approval_recommendation="APPROVE" if quality_score >= 0.8 else "REVISE",
    )


def _setup(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    run = make_agent_run(db, project, node, prompt)
    return node, prompt, run


# --- Stop condition 1: quality threshold met --------------------------------------


def test_loop_stops_after_one_iteration_when_quality_meets_threshold(db, project, monkeypatch):
    node, prompt, run = _setup(db, project)

    monkeypatch.setattr("app.services.loop_engine.generate", lambda **kwargs: _draft_result())
    monkeypatch.setattr("app.services.loop_engine.run_validator", lambda **kwargs: _validation(0.9))

    engine = LoopEngineService(db)
    result = engine.run_loop(
        run=run, project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={}, freeform_context={}, max_iterations=3, quality_threshold=0.8,
    )
    db.flush()

    assert result.loop_status == LoopStatus.COMPLETED_QUALITY_MET
    assert result.iterations_run == 1
    assert result.quality_score == 0.9
    assert result.validation_result["approval_recommendation"] == "APPROVE"
    assert run.loop_status == LoopStatus.COMPLETED_QUALITY_MET
    assert run.loop_iteration == 1
    assert run.loop_validation_result["quality_score"] == 0.9

    steps = [e.step for e in run.loop_events]
    assert steps == [
        LoopStepType.PLAN,
        LoopStepType.RETRIEVE_CONTEXT,
        LoopStepType.GENERATE_DRAFT,
        LoopStepType.VALIDATE,
        LoopStepType.READY_FOR_REVIEW,
    ]
    validate_event = next(e for e in run.loop_events if e.step == LoopStepType.VALIDATE)
    assert validate_event.validation_result["approval_recommendation"] == "APPROVE"


# --- Stop condition 2: max iterations reached --------------------------------------


def test_loop_stops_at_max_iterations_when_quality_never_met(db, project, monkeypatch):
    node, prompt, run = _setup(db, project)
    call_count = {"n": 0}

    def fake_generate(**kwargs):
        call_count["n"] += 1
        return _draft_result(content=f"draft v{call_count['n']}")

    monkeypatch.setattr("app.services.loop_engine.generate", fake_generate)
    monkeypatch.setattr(
        "app.services.loop_engine.run_validator",
        lambda **kwargs: _validation(0.3, critical_issues=["Still missing key content."]),
    )

    engine = LoopEngineService(db)
    result = engine.run_loop(
        run=run, project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={}, freeform_context={}, max_iterations=3, quality_threshold=0.8,
    )
    db.flush()

    assert result.loop_status == LoopStatus.COMPLETED_MAX_ITERATIONS
    assert result.iterations_run == 3
    assert call_count["n"] == 3  # 1 GENERATE_DRAFT + 2 IMPROVE, never a 4th call past max_iterations

    generate_steps = [e.step for e in run.loop_events if e.step == LoopStepType.GENERATE_DRAFT]
    improve_steps = [e.step for e in run.loop_events if e.step == LoopStepType.IMPROVE]
    validate_steps = [e for e in run.loop_events if e.step == LoopStepType.VALIDATE]
    assert len(generate_steps) == 1
    assert len(improve_steps) == 2
    assert len(validate_steps) == 3
    assert all(v.quality_score == 0.3 for v in validate_steps)
    assert all(v.validation_issues == ["Still missing key content."] for v in validate_steps)


# --- Stop condition 3: below threshold but no critical issues ----------------------


def test_loop_stops_early_when_below_threshold_but_no_critical_issues(db, project, monkeypatch):
    node, prompt, run = _setup(db, project)

    monkeypatch.setattr("app.services.loop_engine.generate", lambda **kwargs: _draft_result())
    monkeypatch.setattr(
        "app.services.loop_engine.run_validator",
        lambda **kwargs: _validation(0.5, suggestions=["Could use a bit more detail."]),
    )

    engine = LoopEngineService(db)
    result = engine.run_loop(
        run=run, project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={}, freeform_context={}, max_iterations=5, quality_threshold=0.8,
    )
    db.flush()

    # Would have 4 more iterations available (max_iterations=5), but rule 5
    # says IMPROVE only runs when a critical issue exists — it doesn't here.
    assert result.loop_status == LoopStatus.COMPLETED_NO_CRITICAL_ISSUES
    assert result.iterations_run == 1


# --- Stop condition 4: clarification requested -------------------------------------


def test_loop_stops_immediately_when_draft_needs_clarification(db, project, monkeypatch):
    node, prompt, run = _setup(db, project)
    validate_calls = {"n": 0}

    monkeypatch.setattr(
        "app.services.loop_engine.generate", lambda **kwargs: _draft_result(content="", needs_clarification=True)
    )

    def fake_validate(**kwargs):
        validate_calls["n"] += 1
        return _validation(1.0)

    monkeypatch.setattr("app.services.loop_engine.run_validator", fake_validate)

    engine = LoopEngineService(db)
    result = engine.run_loop(
        run=run, project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={}, freeform_context={}, max_iterations=3, quality_threshold=0.8,
    )
    db.flush()

    assert result.needs_clarification is True
    assert result.loop_status == LoopStatus.WAITING_FOR_CLARIFICATION
    assert validate_calls["n"] == 0  # VALIDATE never runs — nothing to validate yet

    steps = [e.step for e in run.loop_events]
    assert LoopStepType.VALIDATE not in steps
    assert steps[-1] == LoopStepType.READY_FOR_REVIEW


# --- Rule 5: the validator's own suggestions feed the next IMPROVE call -----------


def test_loop_records_improving_scores_across_iterations_as_improvement_history(db, project, monkeypatch):
    node, prompt, run = _setup(db, project)
    call_count = {"n": 0}

    def fake_generate(**kwargs):
        call_count["n"] += 1
        # Rule 5: the validator's own suggestions (not a generic restatement
        # of critical issues) must be what's threaded into the next call.
        if call_count["n"] > 1:
            assert kwargs["validation_feedback"] == ["Add an overview section."]
        return _draft_result(content=f"draft v{call_count['n']}")

    scores = iter([0.4, 0.85])

    def fake_validate(**kwargs):
        score = next(scores)
        if score >= 0.8:
            return _validation(score)
        return _validation(score, critical_issues=["Missing an overview section."], suggestions=["Add an overview section."])

    monkeypatch.setattr("app.services.loop_engine.generate", fake_generate)
    monkeypatch.setattr("app.services.loop_engine.run_validator", fake_validate)

    engine = LoopEngineService(db)
    result = engine.run_loop(
        run=run, project=project, node=node, action=AgentPromptRole.DRAFT, active_prompt=prompt,
        approved_artifact_content={}, approved_artifact_summaries={}, freeform_context={}, max_iterations=5, quality_threshold=0.8,
    )
    db.flush()

    assert result.loop_status == LoopStatus.COMPLETED_QUALITY_MET
    assert result.iterations_run == 2

    validate_scores = [e.quality_score for e in run.loop_events if e.step == LoopStepType.VALIDATE]
    assert validate_scores == [0.4, 0.85]

    draft_contents = [
        e.content_snapshot for e in run.loop_events if e.step in (LoopStepType.GENERATE_DRAFT, LoopStepType.IMPROVE)
    ]
    assert draft_contents == ["draft v1", "draft v2"]
