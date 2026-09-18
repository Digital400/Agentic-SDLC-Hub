"""Phase 16: shadow-mode comparison — see
app/services/agent_migration_shadow.py's module docstring.
"""

import pytest

from app.models import AgentMigrationShadowRun
from app.services import ai_generation
from app.services.agent_migration_shadow import run_requirement_intake_shadow_comparison
from tests.conftest import make_agent_prompt, make_node

FULL_CONTEXT = {
    "business_objective": "Reduce password-reset support tickets by 50%.",
    "users": "End users who forgot their password.",
    "current_problem": "Users must email support to reset their password.",
}


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


def _node(db, project):
    return make_node(db, project, node_key="requirement_intake", order_index=0, output_artifact_type="requirement_intake_document")


class TestRunRequirementIntakeShadowComparison:
    def test_returns_the_legacy_result_for_the_caller_to_persist(self, db, project, actor):
        node = _node(db, project)
        prompt = make_agent_prompt(db, stage="requirement_intake")

        result = run_requirement_intake_shadow_comparison(
            db, project=project, node=node, active_prompt=prompt, freeform_context=FULL_CONTEXT,
            context_token_budget=4000, output_token_budget=1024, triggered_by_user_id=actor.id,
        )
        assert result.content_markdown  # the legacy AgentGenerationResult, unchanged shape

    def test_persists_exactly_one_comparison_row_per_call(self, db, project, actor):
        node = _node(db, project)
        prompt = make_agent_prompt(db, stage="requirement_intake")

        run_requirement_intake_shadow_comparison(
            db, project=project, node=node, active_prompt=prompt, freeform_context=FULL_CONTEXT,
            context_token_budget=4000, output_token_budget=1024, triggered_by_user_id=actor.id,
        )
        db.commit()

        rows = db.query(AgentMigrationShadowRun).filter(AgentMigrationShadowRun.project_id == project.id).all()
        assert len(rows) == 1
        assert rows[0].node_key == "requirement_intake"

    def test_records_both_legacy_and_v2_token_counts(self, db, project, actor):
        node = _node(db, project)
        prompt = make_agent_prompt(db, stage="requirement_intake")

        run_requirement_intake_shadow_comparison(
            db, project=project, node=node, active_prompt=prompt, freeform_context=FULL_CONTEXT,
            context_token_budget=4000, output_token_budget=1024, triggered_by_user_id=actor.id,
        )
        db.commit()

        row = db.query(AgentMigrationShadowRun).filter(AgentMigrationShadowRun.project_id == project.id).one()
        assert row.legacy_total_tokens > 0
        assert row.v2_total_tokens > 0

    def test_flags_matching_clarification_outcomes_when_both_paths_complete(self, db, project, actor):
        node = _node(db, project)
        prompt = make_agent_prompt(db, stage="requirement_intake")

        run_requirement_intake_shadow_comparison(
            db, project=project, node=node, active_prompt=prompt, freeform_context=FULL_CONTEXT,
            context_token_budget=4000, output_token_budget=1024, triggered_by_user_id=actor.id,
        )
        db.commit()

        row = db.query(AgentMigrationShadowRun).filter(AgentMigrationShadowRun.project_id == project.id).one()
        # Legacy mock path never needs clarification; v2's deterministic
        # pre-check passes too (FULL_CONTEXT has all three required fields)
        # — both should agree.
        assert row.clarification_outcomes_matched is True

    def test_a_v2_side_exception_is_recorded_not_raised(self, db, project, actor, monkeypatch):
        node = _node(db, project)
        prompt = make_agent_prompt(db, stage="requirement_intake")

        import app.services.agent_migration_shadow as shadow_module

        def boom(*args, **kwargs):
            raise RuntimeError("simulated v2 failure")

        monkeypatch.setattr(shadow_module, "run_requirement_intake_agent_v2", boom)

        result = run_requirement_intake_shadow_comparison(
            db, project=project, node=node, active_prompt=prompt, freeform_context=FULL_CONTEXT,
            context_token_budget=4000, output_token_budget=1024, triggered_by_user_id=actor.id,
        )
        db.commit()

        assert result.content_markdown  # the caller still gets a real legacy result
        row = db.query(AgentMigrationShadowRun).filter(AgentMigrationShadowRun.project_id == project.id).one()
        assert row.v2_total_tokens == 0
        assert row.extra_data["v2_error"] == "simulated v2 failure"

    def test_human_acceptance_starts_null_never_fabricated(self, db, project, actor):
        node = _node(db, project)
        prompt = make_agent_prompt(db, stage="requirement_intake")

        run_requirement_intake_shadow_comparison(
            db, project=project, node=node, active_prompt=prompt, freeform_context=FULL_CONTEXT,
            context_token_budget=4000, output_token_budget=1024, triggered_by_user_id=actor.id,
        )
        db.commit()

        row = db.query(AgentMigrationShadowRun).filter(AgentMigrationShadowRun.project_id == project.id).one()
        assert row.human_acceptance is None
