"""Regression test for the generic "Run Agent" IMPROVE/VALIDATE action
(app/api/routes/agent_runs.py's start_agent_run) failing to give the
model the artifact's own current content to revise.

Unlike revision_agent.py (the reviewer "Request changes" cycle) and
section_improve_agent.py ("Improve section"), this endpoint never passed
`current_draft_content` to ai_generation.generate() — so an IMPROVE run
degraded into a fresh from-scratch draft over the same upstream inputs,
which (absent fresh review comments) came back looking unchanged. See
ai_generation.generate's own docstring, which already documented IMPROVE
as "a human explicitly asked to revise this stage's own artifact" — the
code just didn't actually wire that up at this entry point.
"""

import app.api.routes.agent_runs as agent_runs
from app.models import (
    AgentPromptRole,
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    LoopStatus,
    WorkflowStatus,
)
from app.schemas.agent_run import AgentRunCreate
from app.services.ai_generation import AgentGenerationResult
from app.services.loop_engine import LoopEngineService, LoopResult
from tests.conftest import make_agent_prompt, make_node

ORIGINAL_DOC = "## Overview\n\nOriginal content.\n\n## Risks\n\nNone identified yet.\n"


def _result(content: str) -> AgentGenerationResult:
    return AgentGenerationResult(
        content_markdown=content, needs_clarification=False, prompt_tokens=100, completion_tokens=50,
        total_tokens=150, cost=0.02, used_mock=True, estimated_context_tokens=120, token_budget_report={},
    )


def _setup(db, project, actor, *, with_validate_prompt=False):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    draft_prompt = make_agent_prompt(db, stage="node_a")
    make_agent_prompt(db, stage="node_a", role=AgentPromptRole.IMPROVE, agent=draft_prompt.agent_definition)
    if with_validate_prompt:
        make_agent_prompt(db, stage="node_a", role=AgentPromptRole.VALIDATE, agent=draft_prompt.agent_definition)

    artifact = Artifact(
        project_id=project.id, workflow_node_id=node.id, artifact_type=node.output_artifact_type,
        title="Node A artifact", status=ArtifactStatus.DRAFT, created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()
    version = ArtifactVersion(artifact_id=artifact.id, version_number=1, content_markdown=ORIGINAL_DOC, created_by_id=actor.id)
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()

    return node, artifact


def test_improve_action_passes_the_artifacts_current_content_to_generate(db, project, actor, monkeypatch):
    node, _artifact = _setup(db, project, actor)

    captured_kwargs = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _result("## Overview\n\nRevised content.\n\n## Risks\n\nNone identified yet.\n")

    monkeypatch.setattr(agent_runs, "generate", fake_generate)
    monkeypatch.setattr(agent_runs, "retrieve_relevant_chunks", lambda *a, **kw: [])

    payload = AgentRunCreate(
        project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.IMPROVE, triggered_by_user_id=actor.id,
    )
    run = agent_runs.start_agent_run(payload, db)

    assert run.status.value == "COMPLETED"
    assert captured_kwargs["current_draft_content"] == ORIGINAL_DOC


def test_validate_action_also_passes_current_content(db, project, actor, monkeypatch):
    node, _artifact = _setup(db, project, actor, with_validate_prompt=True)

    captured_kwargs = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _result("Validation notes.")

    monkeypatch.setattr(agent_runs, "generate", fake_generate)
    monkeypatch.setattr(agent_runs, "retrieve_relevant_chunks", lambda *a, **kw: [])

    payload = AgentRunCreate(
        project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.VALIDATE, triggered_by_user_id=actor.id,
    )
    run = agent_runs.start_agent_run(payload, db)

    assert run.status.value == "COMPLETED"
    assert captured_kwargs["current_draft_content"] == ORIGINAL_DOC


def test_draft_action_does_not_pass_current_draft_content(db, project, actor, monkeypatch):
    """DRAFT is a fresh draft, not a revision — it goes through the Loop
    Engine, not this fix's current_draft_content lookup at all."""
    node, _artifact = _setup(db, project, actor)

    captured_kwargs = {}

    def fake_run_loop(self, **kwargs):
        captured_kwargs.update(kwargs)
        return LoopResult(
            content_markdown="Freshly drafted.", needs_clarification=False, used_mock=True,
            loop_status=LoopStatus.COMPLETED_QUALITY_MET, iterations_run=1, quality_score=1.0,
        )

    monkeypatch.setattr(LoopEngineService, "run_loop", fake_run_loop)
    monkeypatch.setattr(agent_runs, "retrieve_relevant_chunks", lambda *a, **kw: [])

    payload = AgentRunCreate(
        project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.DRAFT, triggered_by_user_id=actor.id,
    )
    run = agent_runs.start_agent_run(payload, db)

    assert run.status.value == "COMPLETED"
    # DRAFT's run_loop call has no current_draft_content kwarg at all —
    # the fix only touches the IMPROVE/VALIDATE branch.
    assert "current_draft_content" not in captured_kwargs
