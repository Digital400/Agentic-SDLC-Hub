"""Unit + route tests for the "Improve section" agent action — see
app/services/section_improve_agent.py and
app/api/routes/artifacts.py's improve_artifact_section.

`generate` and `retrieve_relevant_chunks` are monkeypatched, same pattern
as test_revision_agent.py — this file's own control flow and
section-scoping are under test, not content generation or RAG retrieval.

Key behavioral difference from revision_agent.py, verified explicitly
below: no Review is ever created or touched, and the artifact stays DRAFT
throughout (this tool edits a not-yet-submitted document; "Send for
review" is still the one real submission action).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.artifacts import improve_artifact_section
from app.models import (
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    AuditLog,
    Review,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.artifact import ImproveSectionRequest
from app.services import section_improve_agent
from app.services.ai_generation import AgentGenerationResult
from app.services.section_improve_agent import SectionImproveAgentError, run_section_improve_agent
from tests.conftest import make_agent_prompt, make_node

ORIGINAL_DOC = (
    "## Overview\n\nThis stage covers the initial rollout.\n\n"
    "## Risks\n\nNo risks identified yet.\n\n"
    "## Timeline\n\nQ1 target.\n"
)


def _revised_result(content: str, *, needs_clarification: bool = False) -> AgentGenerationResult:
    return AgentGenerationResult(
        content_markdown=content, needs_clarification=needs_clarification, prompt_tokens=100, completion_tokens=50,
        total_tokens=150, cost=0.02, used_mock=True, estimated_context_tokens=120, token_budget_report={},
    )


def _setup(db, project, actor, *, artifact_status=ArtifactStatus.DRAFT, with_improve_prompt=True):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    draft_prompt = make_agent_prompt(db, stage="node_a")
    if with_improve_prompt:
        from app.models import AgentPromptRole

        make_agent_prompt(db, stage="node_a", role=AgentPromptRole.IMPROVE, agent=draft_prompt.agent_definition)

    artifact = Artifact(
        project_id=project.id, workflow_node_id=node.id, artifact_type=node.output_artifact_type,
        title="Node A artifact", status=artifact_status, created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()

    version = ArtifactVersion(artifact_id=artifact.id, version_number=1, content_markdown=ORIGINAL_DOC, created_by_id=actor.id)
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()

    return node, artifact, version


def _mock_generation(monkeypatch, content: str, *, needs_clarification: bool = False):
    monkeypatch.setattr(section_improve_agent, "generate", lambda **kwargs: _revised_result(content, needs_clarification=needs_clarification))
    monkeypatch.setattr(section_improve_agent, "retrieve_relevant_chunks", lambda *a, **kw: [])


# --- run_section_improve_agent: success path -----------------------------------------------


def test_full_cycle_only_updates_the_named_section(db, project, actor, monkeypatch):
    node, artifact, version = _setup(db, project, actor)
    revised_doc = (
        "## Overview\n\nCOMPLETELY REWRITTEN.\n\n"
        "## Risks\n\nMitigation plan: staged rollout with a kill switch.\n\n"
        "## Timeline\n\nQ2 target now.\n"
    )
    captured_kwargs = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _revised_result(revised_doc)

    monkeypatch.setattr(section_improve_agent, "generate", fake_generate)
    monkeypatch.setattr(section_improve_agent, "retrieve_relevant_chunks", lambda *a, **kw: [])

    result = run_section_improve_agent(db, artifact=artifact, section_title="Risks", instruction="Add mitigations.", triggered_by=actor)

    assert captured_kwargs["current_draft_content"] == ORIGINAL_DOC
    assert captured_kwargs["review_comments"] == ["[Section: Risks] Add mitigations."]

    assert result.needs_clarification is False
    assert result.section_updated == "Risks"
    assert result.artifact_version is not None
    assert "Mitigation plan: staged rollout" in result.artifact_version.content_markdown
    assert "This stage covers the initial rollout." in result.artifact_version.content_markdown
    assert "COMPLETELY REWRITTEN" not in result.artifact_version.content_markdown

    assert result.artifact_version.version_number == 2
    assert artifact.current_version_id == result.artifact_version.id
    # Key difference from revision_agent.py: stays DRAFT, no Review.
    assert artifact.status == ArtifactStatus.DRAFT
    assert node.status == WorkflowStatus.READY
    assert db.query(Review).filter(Review.artifact_version_id == result.artifact_version.id).count() == 0

    assert "Risks" in result.artifact_version.change_summary


def test_needs_clarification_saves_nothing(db, project, actor, monkeypatch):
    node, artifact, version = _setup(db, project, actor)
    _mock_generation(monkeypatch, "", needs_clarification=True)

    result = run_section_improve_agent(db, artifact=artifact, section_title="Risks", instruction="???", triggered_by=actor)

    assert result.needs_clarification is True
    assert result.artifact_version is None
    assert result.section_updated is None
    assert artifact.current_version_id == version.id  # untouched
    assert node.status == WorkflowStatus.WAITING_FOR_INPUT


# --- Preconditions -----------------------------------------------------------------------


def test_rejects_a_non_draft_artifact(db, project, actor):
    node, artifact, version = _setup(db, project, actor, artifact_status=ArtifactStatus.READY_FOR_REVIEW)

    with pytest.raises(SectionImproveAgentError, match="DRAFT"):
        run_section_improve_agent(db, artifact=artifact, section_title="Risks", instruction="x", triggered_by=actor)


def test_rejects_an_unknown_section_title(db, project, actor):
    node, artifact, version = _setup(db, project, actor)

    with pytest.raises(SectionImproveAgentError, match="not found"):
        run_section_improve_agent(db, artifact=artifact, section_title="Nonexistent Section", instruction="x", triggered_by=actor)


def test_rejects_a_headingless_document_even_though_content_technically_matches(db, project, actor):
    """Regression test for a real bug: a document with no `##` headings at
    all gets a synthetic single "Content" section from split_into_sections
    — that's not a genuine, individually-safe-to-improve section, it's the
    entire document. Asking the IMPROVE agent to revise "the Content
    section" of a headingless artifact has previously returned a short,
    scoped-looking response that then replaced the whole document, since
    there was nothing else in the merge to preserve it against."""
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    make_agent_prompt(db, stage="node_a")
    artifact = Artifact(
        project_id=project.id, workflow_node_id=node.id, artifact_type=node.output_artifact_type,
        title="Node A artifact", status=ArtifactStatus.DRAFT, created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()
    version = ArtifactVersion(
        artifact_id=artifact.id, version_number=1, content_markdown="# Clarification Needed\n\nNo headings here at all.",
        created_by_id=actor.id,
    )
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()

    with pytest.raises(SectionImproveAgentError, match="no named sections"):
        run_section_improve_agent(db, artifact=artifact, section_title="Content", instruction="x", triggered_by=actor)


def test_raises_without_an_active_improve_prompt(db, project, actor):
    node, artifact, version = _setup(db, project, actor, with_improve_prompt=False)

    with pytest.raises(SectionImproveAgentError, match="No active improve prompt"):
        run_section_improve_agent(db, artifact=artifact, section_title="Risks", instruction="x", triggered_by=actor)


# --- Route-level: permissions, audit logs -------------------------------------------------


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _qa(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="QA", role=UserRole.QA)
    db.add(user)
    db.flush()
    return user


def test_route_rejects_a_role_outside_stage_edit_roles(db, project, actor, monkeypatch):
    """node_a has no STAGE_EDIT_ROLES entry — the permissions dict defaults
    to "nobody but Admin" for an unlisted node_key (see
    app/services/permissions.py's _require_role)."""
    node, artifact, version = _setup(db, project, actor)
    _mock_generation(monkeypatch, "## Overview\n\nx\n\n## Risks\n\ny\n\n## Timeline\n\nz\n")
    qa = _qa(db)

    with pytest.raises(HTTPException) as exc_info:
        improve_artifact_section(artifact.id, ImproveSectionRequest(section_title="Risks", instruction="x", triggered_by_user_id=qa.id), db)
    assert exc_info.value.status_code == 403


def test_route_404s_for_a_missing_artifact(db, actor):
    with pytest.raises(HTTPException) as exc_info:
        improve_artifact_section(uuid.uuid4(), ImproveSectionRequest(section_title="Risks", instruction="x", triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 404


def test_route_success_records_audit_logs_and_returns_the_new_version(db, project, actor, monkeypatch):
    node, artifact, version = _setup(db, project, actor)
    _mock_generation(monkeypatch, "## Overview\n\nOld.\n\n## Risks\n\nNew mitigation text.\n\n## Timeline\n\nQ1 target.\n")

    response = improve_artifact_section(
        artifact.id, ImproveSectionRequest(section_title="Risks", instruction="Add mitigations.", triggered_by_user_id=actor.id), db,
    )

    assert response.needs_clarification is False
    assert response.section_updated == "Risks"
    assert response.artifact_status == "DRAFT"
    assert response.artifact_version_id is not None

    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert "agent_run.started" in actions
    assert "agent_run.completed" in actions
    assert "artifact_version.created" in actions


def test_route_409s_for_a_non_draft_artifact(db, project, actor, monkeypatch):
    node, artifact, version = _setup(db, project, actor, artifact_status=ArtifactStatus.READY_FOR_REVIEW)

    with pytest.raises(HTTPException) as exc_info:
        improve_artifact_section(artifact.id, ImproveSectionRequest(section_title="Risks", instruction="x", triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
