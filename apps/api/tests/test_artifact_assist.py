"""Summarize changes, Ask questions and Regenerate section — the Documents
page's agent actions (see app/services/artifact_assist.py)."""

import pytest
from fastapi import HTTPException

import app.services.section_improve_agent as section_agent
from app.api.routes.artifacts import ask_about_artifact, get_artifact_changes, improve_artifact_section
from app.models import AgentPromptRole, AgentRun, AgentRunStatus, Artifact, ArtifactStatus, ArtifactVersion, WorkflowStatus
from app.schemas.artifact import AskQuestionRequest, ImproveSectionRequest
from app.services import artifact_assist
from app.services.ai_generation import AgentGenerationResult
from tests.conftest import make_agent_prompt, make_node

V1 = "## Overview\n\nA simple to-do app.\n\n## Constraints\n\n- Budget is low.\n- Launch in two weeks.\n"
V2 = "## Overview\n\nA simple to-do app.\n\n## Constraints\n\n- Budget is low.\n- Launch in four weeks.\n\n## Success Metric\n\nUsers finish tasks.\n"


def _artifact(db, project, actor, *, contents=(V1,)):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    artifact = Artifact(
        project_id=project.id, workflow_node_id=node.id, artifact_type=node.output_artifact_type,
        title="Intake Summary", status=ArtifactStatus.DRAFT, created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()
    version = None
    for n, content in enumerate(contents, start=1):
        version = ArtifactVersion(artifact_id=artifact.id, version_number=n, content_markdown=content, created_by_id=actor.id)
        db.add(version)
        db.flush()
    artifact.current_version_id = version.id
    db.flush()
    db.refresh(artifact)
    return node, artifact


# --- Summarize changes --------------------------------------------------------------------


def test_changes_first_version_has_nothing_to_compare(db, project, actor):
    _, artifact = _artifact(db, project, actor)
    result = get_artifact_changes(artifact.id, None, None, db)
    assert result.is_first_version and result.changes == [] and "first version" in result.headline


def test_changes_reports_changed_and_added_sections_with_lines(db, project, actor):
    _, artifact = _artifact(db, project, actor, contents=(V1, V2))
    result = get_artifact_changes(artifact.id, None, None, db)

    assert (result.from_version, result.to_version) == (1, 2)
    by_title = {c.title: c for c in result.changes}
    assert set(by_title) == {"Constraints", "Success Metric"}
    assert by_title["Constraints"].kind == "changed"
    assert by_title["Constraints"].added_lines == ["- Launch in four weeks."]
    assert by_title["Constraints"].removed_lines == ["- Launch in two weeks."]
    assert by_title["Success Metric"].kind == "added"
    assert "1 section changed" in result.headline and "1 section added" in result.headline


def test_changes_reports_removed_and_identical(db, project, actor):
    _, artifact = _artifact(db, project, actor, contents=(V2, V1))
    result = get_artifact_changes(artifact.id, None, None, db)
    assert {c.title: c.kind for c in result.changes} == {"Constraints": "changed", "Success Metric": "removed"}

    _, same = _artifact(db, project, actor, contents=(V1, V1))
    assert "No differences" in get_artifact_changes(same.id, None, None, db).headline


def test_changes_unknown_version_is_404(db, project, actor):
    _, artifact = _artifact(db, project, actor, contents=(V1, V2))
    with pytest.raises(HTTPException) as exc:
        get_artifact_changes(artifact.id, 9, None, db)
    assert exc.value.status_code == 404


# --- Ask questions ------------------------------------------------------------------------


def test_ask_offline_returns_relevant_passages_and_does_not_modify(db, project, actor, monkeypatch):
    monkeypatch.setattr(artifact_assist, "get_active_provider", lambda: "mock")
    _, artifact = _artifact(db, project, actor, contents=(V2,))

    result = ask_about_artifact(artifact.id, AskQuestionRequest(question="What is the budget constraint?", triggered_by_user_id=actor.id), db)

    assert result.used_mock and "Constraints" in result.sources and "Budget is low" in result.answer
    db.refresh(artifact)
    assert artifact.current_version.content_markdown == V2 and len(artifact.versions) == 1


def test_ask_offline_says_so_when_nothing_relates(db, project, actor, monkeypatch):
    monkeypatch.setattr(artifact_assist, "get_active_provider", lambda: "mock")
    _, artifact = _artifact(db, project, actor)
    result = ask_about_artifact(artifact.id, AskQuestionRequest(question="zebra quantum", triggered_by_user_id=actor.id), db)
    assert result.sources == [] and "couldn't find" in result.answer


def test_ask_with_provider_sends_document_and_original_request_and_names_sources(db, project, actor, monkeypatch):
    monkeypatch.setattr(artifact_assist, "get_active_provider", lambda: "anthropic")
    node, artifact = _artifact(db, project, actor)
    prompt = make_agent_prompt(db, stage="node_a")
    db.add(AgentRun(
        project_id=project.id, workflow_node_id=node.id, agent_definition_id=prompt.agent_definition_id,
        triggered_by_user_id=actor.id, action=AgentPromptRole.DRAFT, status=AgentRunStatus.COMPLETED,
        input_context={"stakeholder_request": "Kanishka wants a to-do app."}, input_artifact_ids=[],
    ))
    db.flush()
    seen = {}

    def fake_raw(**kwargs):
        seen.update(kwargs)
        return "The budget is low [Constraints]."

    monkeypatch.setattr(artifact_assist, "generate_raw_text", fake_raw)
    result = ask_about_artifact(artifact.id, AskQuestionRequest(question="Budget?", triggered_by_user_id=actor.id), db)

    assert result.sources == ["Constraints"] and not result.used_mock
    assert "Budget is low" in seen["user_content"] and "Kanishka wants a to-do app." in seen["user_content"]


# --- Regenerate section -------------------------------------------------------------------


def test_regenerate_requires_no_instruction_but_improve_does():
    from pydantic import ValidationError

    base = dict(section_title="Constraints", triggered_by_user_id="00000000-0000-0000-0000-000000000001")
    assert ImproveSectionRequest(**base, mode="regenerate").instruction == ""
    with pytest.raises(ValidationError):
        ImproveSectionRequest(**base, mode="improve")


def test_regenerate_uses_original_request_and_only_changes_that_section(db, project, actor, monkeypatch):
    node, artifact = _artifact(db, project, actor)
    draft = make_agent_prompt(db, stage="node_a")
    make_agent_prompt(db, stage="node_a", role=AgentPromptRole.IMPROVE, agent=draft.agent_definition)
    db.add(AgentRun(
        project_id=project.id, workflow_node_id=node.id, agent_definition_id=draft.agent_definition_id,
        triggered_by_user_id=actor.id, action=AgentPromptRole.DRAFT, status=AgentRunStatus.COMPLETED,
        input_context={"stakeholder_request": "Kanishka wants a to-do app.", "clarification_answers": "Budget is tiny."},
        input_artifact_ids=[],
    ))
    db.flush()
    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        # The model "rewrites" everything; only Constraints may survive the merge.
        return AgentGenerationResult(
            content_markdown="## Overview\n\nCHANGED OVERVIEW\n\n## Constraints\n\n- Fresh constraint.\n", needs_clarification=False,
            prompt_tokens=1, completion_tokens=1, total_tokens=2, cost=0.0, used_mock=True, estimated_context_tokens=1, token_budget_report={},
        )

    monkeypatch.setattr(section_agent, "generate", fake_generate)
    monkeypatch.setattr(section_agent, "retrieve_relevant_chunks", lambda *a, **kw: [])

    response = improve_artifact_section(
        artifact.id, ImproveSectionRequest(section_title="Constraints", instruction="", triggered_by_user_id=actor.id, mode="regenerate"), db
    )

    assert response.section_updated == "Constraints"
    assert seen["freeform_context"] == {"stakeholder_request": "Kanishka wants a to-do app.", "clarification_answers": "Budget is tiny."}
    assert "from scratch" in seen["review_comments"][0]
    db.refresh(artifact)
    text = artifact.current_version.content_markdown
    assert "Fresh constraint." in text and "A simple to-do app." in text and "CHANGED OVERVIEW" not in text
    assert artifact.current_version.change_summary == 'Regenerated section "Constraints".'
