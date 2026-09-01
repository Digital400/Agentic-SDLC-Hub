"""Unit tests for the AI Ops Dashboard's aggregation logic — see
app/services/ops_metrics.py.
"""

from datetime import datetime, timedelta, timezone

from app.models import AgentRunLoopEvent, AgentRunStatus, LoopStatus, LoopStepType, WorkflowStatus
from app.services.ops_metrics import build_ops_summary
from tests.conftest import make_agent_prompt, make_agent_run, make_node


def _completed_run(db, project, node, prompt, *, cost=0.0, total_tokens=None, quality_score=None, sources=None):
    run = make_agent_run(db, project, node, prompt)
    run.status = AgentRunStatus.COMPLETED
    run.cost = cost
    if total_tokens is not None:
        run.token_usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": total_tokens}
    if quality_score is not None:
        run.loop_quality_score = quality_score
        run.loop_status = LoopStatus.COMPLETED_QUALITY_MET
        run.loop_iteration = 1
    if sources is not None:
        run.retrieved_sources = sources
    now = datetime.now(timezone.utc)
    run.started_at = now
    run.completed_at = now + timedelta(seconds=1)
    db.flush()
    return run


def _failed_run(db, project, node, prompt):
    run = make_agent_run(db, project, node, prompt)
    run.status = AgentRunStatus.FAILED
    run.error_message = "AI generation failed"
    db.flush()
    return run


# --- Run volume / rates -------------------------------------------------------------


def test_success_and_failure_rates(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    _completed_run(db, project, node, prompt, cost=0.01)
    _completed_run(db, project, node, prompt, cost=0.01)
    _completed_run(db, project, node, prompt, cost=0.01)
    _failed_run(db, project, node, prompt)

    summary = build_ops_summary(db)

    assert summary.total_runs == 4
    assert summary.successful_runs == 3
    assert summary.failed_runs == 1
    assert summary.success_rate == 0.75
    assert summary.failure_rate == 0.25


def test_rates_are_none_with_zero_runs(db):
    summary = build_ops_summary(db)

    assert summary.total_runs == 0
    assert summary.success_rate is None
    assert summary.failure_rate is None
    assert summary.avg_quality_score is None
    assert summary.avg_tokens_per_run is None


# --- Averages ------------------------------------------------------------------------


def test_avg_quality_score_only_counts_runs_that_have_one(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    _completed_run(db, project, node, prompt, quality_score=0.8)
    _completed_run(db, project, node, prompt, quality_score=1.0)
    _completed_run(db, project, node, prompt)  # no quality score at all (e.g. a VALIDATE-action run)

    summary = build_ops_summary(db)

    assert summary.avg_quality_score == 0.9


def test_avg_tokens_per_run_ignores_runs_with_no_token_usage(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    _completed_run(db, project, node, prompt, total_tokens=100)
    _completed_run(db, project, node, prompt, total_tokens=300)
    _failed_run(db, project, node, prompt)  # never got token usage

    summary = build_ops_summary(db)

    assert summary.total_tokens == 400
    assert summary.avg_tokens_per_run == 200.0


# --- Cost by project ------------------------------------------------------------------


def test_cost_by_project_sums_and_sorts_highest_first(db, project, actor):
    from app.models import Project, ProjectStatus

    other_project = Project(
        name="Other Project", business_owner="Other Owner", workflow_template_id="t", workflow_template_version="1",
        current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor.id,
    )
    db.add(other_project)
    db.flush()

    node_a = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt_a = make_agent_prompt(db, stage="node_a")
    _completed_run(db, project, node_a, prompt_a, cost=0.50)
    _completed_run(db, project, node_a, prompt_a, cost=0.25)

    node_b = make_node(db, other_project, node_key="node_b", order_index=0, status=WorkflowStatus.READY)
    prompt_b = make_agent_prompt(db, stage="node_b")
    _completed_run(db, other_project, node_b, prompt_b, cost=5.00)

    summary = build_ops_summary(db)

    assert [c.project_name for c in summary.cost_by_project] == ["Other Project", "Test Project"]
    assert summary.cost_by_project[0].total_cost == 5.00
    assert summary.cost_by_project[1].total_cost == 0.75
    assert summary.cost_by_project[1].run_count == 2


# --- Most common validation issues / RAG sources ---------------------------------------


def test_validation_issue_frequency_counts_across_every_validate_step(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    run = make_agent_run(db, project, node, prompt)

    for issues in (["Missing risk coverage"], ["Missing risk coverage", "Too short"], ["Missing risk coverage"]):
        db.add(AgentRunLoopEvent(agent_run_id=run.id, iteration=1, step=LoopStepType.VALIDATE, validation_issues=issues))
    db.flush()

    summary = build_ops_summary(db)

    assert summary.validation_issue_frequency[0].message == "Missing risk coverage"
    assert summary.validation_issue_frequency[0].count == 3
    assert summary.validation_issue_frequency[1].message == "Too short"
    assert summary.validation_issue_frequency[1].count == 1


def test_rag_source_usage_counts_citations_across_runs(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    _completed_run(db, project, node, prompt, sources=[{"source_title": "Handbook"}, {"source_title": "UI Guide"}])
    _completed_run(db, project, node, prompt, sources=[{"source_title": "Handbook"}])

    summary = build_ops_summary(db)

    usage_by_title = {u.source_title: u.count for u in summary.rag_source_usage}
    assert usage_by_title == {"Handbook": 2, "UI Guide": 1}


# --- Blocked workflows -----------------------------------------------------------------


def test_blocked_workflows_lists_project_stage_and_reason(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.BLOCKED)
    node.blocked_reason = "Review rejected: missing acceptance criteria"
    db.flush()

    summary = build_ops_summary(db)

    assert summary.blocked_workflow_count == 1
    assert summary.blocked_workflows[0].project_name == project.name
    assert summary.blocked_workflows[0].node_key == "node_a"
    assert summary.blocked_workflows[0].blocked_reason == "Review rejected: missing acceptance criteria"


# --- Stage performance enrichment -------------------------------------------------------


def test_stage_performance_includes_cost_quality_and_iterations(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a")
    _completed_run(db, project, node, prompt, cost=1.0, quality_score=0.9)
    _completed_run(db, project, node, prompt, cost=2.0, quality_score=0.7)

    summary = build_ops_summary(db)

    stage = next(s for s in summary.stage_performance if s.node_key == "node_a")
    assert stage.total_cost == 3.0
    assert stage.avg_quality_score == 0.8
    assert stage.avg_iterations == 1.0
