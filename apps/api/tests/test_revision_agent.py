"""Unit tests for the review-gate's agentic revision loop — see
app/services/revision_agent.py and app/api/routes/reviews.py's
request_changes / run_revision_agent_endpoint.

`generate` and `retrieve_relevant_chunks` are monkeypatched, same pattern
as test_loop_engine.py / test_context_builder.py — the revision loop's own
control flow and section-scoping (rules 1-9) are under test here, not
content generation or RAG retrieval (which needs real Postgres/pgvector),
which have their own test coverage elsewhere.
"""

import pytest

from app.models import (
    AgentDefinition,
    AgentPromptRole,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    Review,
    ReviewComment,
    ReviewStatus,
    WorkflowStatus,
)
from app.schemas.review import ReviewCommentInput, ReviewRequestChangesRequest
from app.services import revision_agent
from app.services.ai_generation import AgentGenerationResult
from app.services.revision_agent import RevisionAgentError, merge_section_revisions, run_revision_agent, split_into_sections
from tests.conftest import make_agent_prompt, make_node

ORIGINAL_DOC = (
    "## Overview\n\nThis stage covers the initial rollout.\n\n"
    "## Risks\n\nNo risks identified yet.\n\n"
    "## Timeline\n\nQ1 target.\n"
)


def _revised_result(content: str, *, needs_clarification: bool = False) -> AgentGenerationResult:
    return AgentGenerationResult(
        content_markdown=content,
        needs_clarification=needs_clarification,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost=0.02,
        used_mock=True,
        estimated_context_tokens=120,
        token_budget_report={},
    )


def _setup(db, project, actor, *, node_status=WorkflowStatus.NEEDS_CHANGES, with_improve_prompt=True):
    node = make_node(db, project, node_key="node_a", order_index=0, status=node_status)
    draft_prompt = make_agent_prompt(db, stage="node_a", role=AgentPromptRole.DRAFT)
    if with_improve_prompt:
        make_agent_prompt(db, stage="node_a", role=AgentPromptRole.IMPROVE, agent=draft_prompt.agent_definition)

    artifact = Artifact(
        project_id=project.id,
        workflow_node_id=node.id,
        artifact_type=node.output_artifact_type,
        title="Node A artifact",
        status=ArtifactStatus.NEEDS_CHANGES,
        created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()

    version = ArtifactVersion(
        artifact_id=artifact.id, version_number=1, content_markdown=ORIGINAL_DOC, created_by_id=actor.id
    )
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()

    review = Review(
        artifact_version_id=version.id,
        workflow_node_id=node.id,
        reviewer_id=actor.id,
        status=ReviewStatus.NEEDS_CHANGES,
    )
    db.add(review)
    db.flush()

    return node, artifact, version, review


def _add_comments(db, review, actor, comments: list[tuple[str, str | None]]):
    for body, section_title in comments:
        db.add(ReviewComment(review_id=review.id, author_id=actor.id, body=body, section_title=section_title))
    db.flush()
    db.refresh(review)


# --- merge_section_revisions / split_into_sections --------------------------------


def test_split_into_sections_matches_heading_structure():
    sections = split_into_sections(ORIGINAL_DOC)
    assert [s["title"] for s in sections] == ["Overview", "Risks", "Timeline"]
    assert "initial rollout" in sections[0]["content"]


def test_merge_only_updates_named_sections():
    """Rule 6: even when the model rewrites every section, only the
    section(s) a reviewer actually named get applied."""
    revised = (
        "## Overview\n\nCOMPLETELY DIFFERENT OVERVIEW.\n\n"
        "## Risks\n\nMitigation plan added for the top three risks.\n\n"
        "## Timeline\n\nQ2 target now.\n"
    )
    merged, changed = merge_section_revisions(
        original_markdown=ORIGINAL_DOC, revised_markdown=revised, affected_section_titles={"Risks"}
    )
    assert changed == ["Risks"]
    assert "Mitigation plan added" in merged
    # Untouched sections carried over verbatim from the original, not the model's rewrite.
    assert "This stage covers the initial rollout." in merged
    assert "COMPLETELY DIFFERENT OVERVIEW" not in merged
    assert "Q1 target." in merged
    assert "Q2 target now." not in merged


def test_merge_is_case_insensitive_on_section_title():
    revised = "## Overview\n\nNew overview.\n\n## risks\n\nNew risk text.\n\n## Timeline\n\nQ1 target.\n"
    merged, changed = merge_section_revisions(
        original_markdown=ORIGINAL_DOC, revised_markdown=revised, affected_section_titles={"RISKS"}
    )
    assert changed == ["Risks"]
    assert "New risk text." in merged
    assert "New overview." not in merged


def test_merge_with_no_named_sections_uses_full_revision():
    """No comment could be linked to a section — nothing to scope a
    partial update to, so the whole revised document is used."""
    revised = "## Overview\n\nRewritten.\n"
    merged, changed = merge_section_revisions(original_markdown=ORIGINAL_DOC, revised_markdown=revised, affected_section_titles=set())
    assert merged == revised
    assert changed == ["Overview"]


def test_merge_appends_a_newly_named_section_not_in_the_original():
    revised = ORIGINAL_DOC + "\n## Rollback Plan\n\nRevert via feature flag.\n"
    merged, changed = merge_section_revisions(
        original_markdown=ORIGINAL_DOC, revised_markdown=revised, affected_section_titles={"Rollback Plan"}
    )
    assert changed == ["Rollback Plan"]
    assert "Revert via feature flag." in merged
    # Nothing else changed.
    assert "No risks identified yet." in merged


# --- ReviewRequestChangesRequest schema (rule 1) -----------------------------------


def test_request_changes_schema_requires_at_least_one_comment():
    with pytest.raises(ValueError):
        ReviewRequestChangesRequest(comments=[])


def test_request_changes_schema_accepts_section_linked_comments():
    req = ReviewRequestChangesRequest(
        comments=[ReviewCommentInput(body="Add a rollback plan.", section_title="Risks"), ReviewCommentInput(body="Tighten the intro.")]
    )
    assert req.comments[0].section_title == "Risks"
    assert req.comments[1].section_title is None


# --- run_revision_agent — the full cycle (rules 4-9) --------------------------------


def test_run_revision_agent_full_cycle(db, project, actor, monkeypatch):
    node, artifact, version, review = _setup(db, project, actor)
    _add_comments(
        db,
        review,
        actor,
        [("No risks identified yet is not acceptable — add mitigations.", "Risks"), ("Overall looks promising.", None)],
    )

    revised_doc = (
        "## Overview\n\nCOMPLETELY REWRITTEN OVERVIEW.\n\n"
        "## Risks\n\nMitigation plan: staged rollout with a kill switch.\n\n"
        "## Timeline\n\nQ2 target now.\n"
    )
    captured_kwargs = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _revised_result(revised_doc)

    monkeypatch.setattr(revision_agent, "generate", fake_generate)
    monkeypatch.setattr(revision_agent, "retrieve_relevant_chunks", lambda *args, **kwargs: [])

    result = run_revision_agent(db, review=review, triggered_by=actor)

    # Rule 5: the revision agent receives the current artifact, the
    # structured comments, and stage rules — verified via the actual
    # generate() call it made.
    assert captured_kwargs["current_draft_content"] == ORIGINAL_DOC
    assert any("Section: Risks" in c for c in captured_kwargs["review_comments"])
    assert any("General feedback" in c for c in captured_kwargs["review_comments"])
    assert captured_kwargs["action"] == AgentPromptRole.IMPROVE

    # Rule 6: only the Risks section was actually replaced.
    assert result.sections_updated == ["Risks"]
    assert result.needs_clarification is False
    assert result.artifact_version is not None
    assert "Mitigation plan: staged rollout" in result.artifact_version.content_markdown
    assert "This stage covers the initial rollout." in result.artifact_version.content_markdown
    assert "COMPLETELY REWRITTEN OVERVIEW" not in result.artifact_version.content_markdown

    # Rule 7: a new artifact version was created and made current.
    assert result.artifact_version.version_number == 2
    assert artifact.current_version_id == result.artifact_version.id
    assert artifact.status == ArtifactStatus.READY_FOR_REVIEW

    # Rule 8: change summary mentions what was addressed.
    assert "2 reviewer comments" in result.artifact_version.change_summary
    assert "Risks" in result.artifact_version.change_summary


def test_revision_agent_includes_engineering_setup_context(db, project, actor, monkeypatch):
    """A reviewer-requested revision is a real IMPROVE run — it should get
    the same Project Engineering Setup context (see
    app/services/agent_context_builder.py) the generic Run Agent
    Draft/Improve/Validate action already gets, not none at all."""
    from app.models import ProjectCodingStandard, ProjectEngineeringSetup

    setup = ProjectEngineeringSetup(
        project_id=project.id, created_by_id=actor.id, application_type="Web Application", primary_language="Python",
    )
    db.add(setup)
    db.flush()
    db.add(ProjectCodingStandard(setup_id=setup.id, title="Naming", content="Use snake_case."))
    db.flush()

    node, artifact, version, review = _setup(db, project, actor)
    _add_comments(db, review, actor, [("Add mitigations.", "Risks")])

    captured_kwargs = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _revised_result(ORIGINAL_DOC)

    monkeypatch.setattr(revision_agent, "generate", fake_generate)
    monkeypatch.setattr(revision_agent, "retrieve_relevant_chunks", lambda *args, **kwargs: [])

    result = run_revision_agent(db, review=review, triggered_by=actor)

    assert "Use snake_case." in captured_kwargs["approved_artifact_content"]["project_engineering_setup"]
    assert result.agent_run.engineering_setup_context_snapshot is not None
    assert result.agent_run.engineering_setup_context_snapshot["agent_type"] == "generic"

    # Rule 3/9: node moved on, and a fresh review was opened for the same reviewer.
    assert node.status == WorkflowStatus.WAITING_FOR_REVIEW
    assert result.new_review is not None
    assert result.new_review.status == ReviewStatus.PENDING
    assert result.new_review.reviewer_id == actor.id
    assert result.new_review.artifact_version_id == result.artifact_version.id

    # The AgentRun itself is a real, auditable IMPROVE run.
    assert result.agent_run.status == AgentRunStatus.COMPLETED
    assert result.agent_run.action == AgentPromptRole.IMPROVE
    assert result.agent_run.output_artifact_id == artifact.id


def test_run_revision_agent_uses_last_validation_result_as_feedback(db, project, actor, monkeypatch):
    node, artifact, version, review = _setup(db, project, actor)
    _add_comments(db, review, actor, [("Please address the validator's findings.", None)])

    # A prior DRAFT run that went through validation — its result should
    # feed into this revision as "validation result" (rule 5).
    agent = db.query(AgentDefinition).filter(AgentDefinition.agent_key == node.agent_key).first()
    validated_run = AgentRun(
        project_id=project.id,
        workflow_node_id=node.id,
        agent_definition_id=agent.id,
        action=AgentPromptRole.DRAFT,
        loop_validation_result={
            "quality_score": 0.6,
            "critical_issues": ["Missing rollback plan."],
            "suggestions": ["Add a monitoring section."],
        },
    )
    db.add(validated_run)
    db.flush()

    captured_kwargs = {}

    def fake_generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _revised_result("## Overview\n\nSame.\n\n## Risks\n\nSame.\n\n## Timeline\n\nSame.\n")

    monkeypatch.setattr(revision_agent, "generate", fake_generate)
    monkeypatch.setattr(revision_agent, "retrieve_relevant_chunks", lambda *args, **kwargs: [])

    run_revision_agent(db, review=review, triggered_by=actor)

    feedback = captured_kwargs["validation_feedback"]
    assert any("Missing rollback plan." in f for f in feedback)
    assert any("Add a monitoring section." in f for f in feedback)


def test_run_revision_agent_needs_clarification_does_not_resubmit(db, project, actor, monkeypatch):
    node, artifact, version, review = _setup(db, project, actor)
    _add_comments(db, review, actor, [("Please clarify the scope.", None)])

    monkeypatch.setattr(revision_agent, "generate", lambda **kwargs: _revised_result("", needs_clarification=True))
    monkeypatch.setattr(revision_agent, "retrieve_relevant_chunks", lambda *args, **kwargs: [])

    result = run_revision_agent(db, review=review, triggered_by=actor)

    assert result.needs_clarification is True
    assert result.artifact_version is None
    assert result.new_review is None
    assert node.status == WorkflowStatus.WAITING_FOR_INPUT
    # Nothing new was saved — the artifact is untouched.
    assert artifact.current_version_id == version.id
    assert artifact.status == ArtifactStatus.NEEDS_CHANGES


def test_run_revision_agent_fails_when_review_not_needs_changes(db, project, actor, monkeypatch):
    node, artifact, version, review = _setup(db, project, actor)
    _add_comments(db, review, actor, [("Some feedback.", None)])
    review.status = ReviewStatus.PENDING
    db.flush()

    generate_called = False

    def fake_generate(**kwargs):
        nonlocal generate_called
        generate_called = True
        return _revised_result("unused")

    monkeypatch.setattr(revision_agent, "generate", fake_generate)

    result = run_revision_agent(db, review=review, triggered_by=actor)

    assert result.agent_run.status == AgentRunStatus.FAILED
    assert "NEEDS_CHANGES" in result.agent_run.error_message
    assert generate_called is False
    assert node.status == WorkflowStatus.NEEDS_CHANGES  # untouched


def test_run_revision_agent_fails_when_node_not_needs_changes(db, project, actor):
    node, artifact, version, review = _setup(db, project, actor, node_status=WorkflowStatus.READY)
    _add_comments(db, review, actor, [("Some feedback.", None)])

    result = run_revision_agent(db, review=review, triggered_by=actor)

    assert result.agent_run.status == AgentRunStatus.FAILED
    assert "Workflow node must be NEEDS_CHANGES" in result.agent_run.error_message


def test_run_revision_agent_fails_with_no_comments(db, project, actor):
    node, artifact, version, review = _setup(db, project, actor)
    # No comments added.

    result = run_revision_agent(db, review=review, triggered_by=actor)

    assert result.agent_run.status == AgentRunStatus.FAILED
    assert "no reviewer comments" in result.agent_run.error_message


def test_run_revision_agent_fails_without_active_improve_prompt(db, project, actor):
    node, artifact, version, review = _setup(db, project, actor, with_improve_prompt=False)
    _add_comments(db, review, actor, [("Some feedback.", None)])

    result = run_revision_agent(db, review=review, triggered_by=actor)

    assert result.agent_run.status == AgentRunStatus.FAILED
    assert "No active improve prompt" in result.agent_run.error_message


def test_run_revision_agent_raises_when_no_agent_configured(db, project, actor):
    node = make_node(db, project, node_key="orphan", order_index=0, status=WorkflowStatus.NEEDS_CHANGES)
    # No AgentDefinition/AgentPrompt at all for node.agent_key.
    artifact = Artifact(
        project_id=project.id,
        workflow_node_id=node.id,
        artifact_type=node.output_artifact_type,
        title="Orphan artifact",
        status=ArtifactStatus.NEEDS_CHANGES,
        created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()
    version = ArtifactVersion(artifact_id=artifact.id, version_number=1, content_markdown=ORIGINAL_DOC, created_by_id=actor.id)
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    db.flush()
    review = Review(artifact_version_id=version.id, workflow_node_id=node.id, reviewer_id=actor.id, status=ReviewStatus.NEEDS_CHANGES)
    db.add(review)
    db.flush()

    with pytest.raises(RevisionAgentError, match="No agent is configured"):
        run_revision_agent(db, review=review, triggered_by=actor)
