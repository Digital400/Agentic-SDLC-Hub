"""Unit tests for ContextBuilderService — see
app/services/context_builder.py.

`retrieve_relevant_chunks` is monkeypatched (it needs real
Postgres/pgvector for cosine_distance — not available in the in-memory
SQLite fixture) so these tests can control exactly which RAG chunks are
"retrieved", independent of embeddings/pgvector. Everything else (project,
node, artifacts, review comments) is real ORM data through the shared
fixtures.
"""

from app.models import ArtifactStatus, Review, ReviewComment, ReviewStatus, WorkflowStatus
from app.services.context_builder import ContextBuilderError, ContextBuilderService
from app.services.retrieval import RetrievedChunk
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node


def _chunk(source_title: str, content: str, similarity: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{source_title}",
        source_id=f"source-{source_title}",
        source_title=source_title,
        chunk_index=0,
        content=content,
        similarity=similarity,
    )


def _add_review_comments(db, node, actor, bodies: list[str], *, decided: bool = True):
    """A minimal, already-decided review with comments, so
    fetch_recent_review_comments (step 5) has something to find."""
    artifact = make_approved_artifact(db, node.project, node, actor)
    review = Review(
        artifact_version_id=artifact.current_version_id,
        workflow_node_id=node.id,
        reviewer_id=actor.id,
        status=ReviewStatus.APPROVED if decided else ReviewStatus.PENDING,
    )
    db.add(review)
    db.flush()
    for body in bodies:
        db.add(ReviewComment(review_id=review.id, author_id=actor.id, body=body))
    db.flush()
    return review


def _setup(db, project, *, checklist=None):
    node = make_node(db, project, node_key="node_a", order_index=0, status=WorkflowStatus.READY)
    prompt = make_agent_prompt(db, stage="node_a", checklist=checklist)
    return node, prompt


# --- Basic shape / all nine output fields ------------------------------------------


def test_build_returns_all_nine_required_fields(db, project, monkeypatch):
    node, prompt = _setup(db, project)
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: [])

    result = ContextBuilderService(db).build(project_id=project.id, node_id=node.id, active_prompt=prompt)

    assert result.system_prompt == prompt.system_prompt
    assert result.output_format == prompt.output_format
    assert result.validation_checklist == prompt.validation_checklist
    assert "node_a" in result.stage_rules
    assert project.name in result.project_summary
    assert result.required_artifact_summaries == {}
    assert result.human_comments == []
    assert result.rag_context == []
    assert isinstance(result.token_budget_report, dict)
    assert "blocks" in result.token_budget_report


def test_raises_for_a_nonexistent_project_or_node(db, project, monkeypatch):
    node, prompt = _setup(db, project)
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: [])
    service = ContextBuilderService(db)

    import uuid

    try:
        service.build(project_id=uuid.uuid4(), node_id=node.id, active_prompt=prompt)
        assert False, "expected ContextBuilderError"
    except ContextBuilderError:
        pass

    try:
        service.build(project_id=project.id, node_id=uuid.uuid4(), active_prompt=prompt)
        assert False, "expected ContextBuilderError"
    except ContextBuilderError:
        pass


# --- Context prioritization ---------------------------------------------------------


def test_required_artifact_uses_summary_by_default(db, project, actor, monkeypatch):
    upstream = make_node(db, project, node_key="upstream", order_index=0, output_artifact_type="upstream_doc")
    node = make_node(
        db, project, node_key="node_a", order_index=1, status=WorkflowStatus.READY, required_inputs=["upstream_doc"]
    )
    artifact = make_approved_artifact(db, project, upstream, actor, content="Full upstream content, quite long.")
    artifact.current_version.agent_context_summary = "Short summary of upstream content."
    db.flush()
    prompt = make_agent_prompt(db, stage="node_a")
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: [])

    result = ContextBuilderService(db).build(project_id=project.id, node_id=node.id, active_prompt=prompt)

    assert result.required_artifact_summaries["upstream_doc"] == "Short summary of upstream content."


def test_required_artifact_escalates_to_full_content_when_configured(db, project, actor, monkeypatch):
    upstream = make_node(db, project, node_key="upstream", order_index=0, output_artifact_type="upstream_doc")
    node = make_node(
        db, project, node_key="node_a", order_index=1, status=WorkflowStatus.READY, required_inputs=["upstream_doc"]
    )
    artifact = make_approved_artifact(db, project, upstream, actor, content="Full upstream content, quite long.")
    artifact.current_version.agent_context_summary = "Short summary of upstream content."
    db.flush()
    prompt = make_agent_prompt(db, stage="node_a")
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: [])

    result = ContextBuilderService(db).build(
        project_id=project.id, node_id=node.id, active_prompt=prompt,
        full_content_artifact_types={"upstream_doc"},
    )

    assert result.required_artifact_summaries["upstream_doc"] == "Full upstream content, quite long."


def test_force_full_content_overrides_node_config_for_every_artifact(db, project, actor, monkeypatch):
    upstream = make_node(db, project, node_key="upstream", order_index=0, output_artifact_type="upstream_doc")
    node = make_node(
        db, project, node_key="node_a", order_index=1, status=WorkflowStatus.READY, required_inputs=["upstream_doc"]
    )
    artifact = make_approved_artifact(db, project, upstream, actor, content="FULL CONTENT")
    artifact.current_version.agent_context_summary = "summary"
    db.flush()
    prompt = make_agent_prompt(db, stage="node_a")
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: [])

    result = ContextBuilderService(db).build(
        project_id=project.id, node_id=node.id, active_prompt=prompt, force_full_content=True
    )

    assert result.required_artifact_summaries["upstream_doc"] == "FULL CONTENT"


def test_rag_context_and_human_comments_are_populated(db, project, actor, monkeypatch):
    node, prompt = _setup(db, project)
    chunks = [_chunk("Handbook", "Some relevant excerpt.", 0.5)]
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: chunks)
    _add_review_comments(db, node, actor, ["Please clarify the scope.", "Add a rollback plan."])

    result = ContextBuilderService(db).build(project_id=project.id, node_id=node.id, active_prompt=prompt)

    assert result.rag_context == [
        {
            "source_id": "source-Handbook",
            "source_title": "Handbook",
            "chunk_id": "chunk-Handbook",
            "content": "Some relevant excerpt.",
            "similarity": 0.5,
            "content_type": "OTHER",
            "stage": None,
            "tags": None,
        }
    ]
    assert result.human_comments == ["Please clarify the scope.", "Add a rollback plan."]


# --- Budget trimming ------------------------------------------------------------------


def _stage_rules_text(node) -> str:
    return (
        f"# Current stage: {node.name} (`{node.node_key}`)\n"
        f"{node.description}\n"
        f"Output artifact type to produce: `{node.output_artifact_type}`"
    )


def _project_summary_text(project) -> str:
    return (
        f"# Project: {project.name}\n"
        f"Business owner: {project.business_owner}\n"
        f"Project description: {project.description or 'Not provided.'}"
    )


def test_low_priority_sections_dropped_before_high_priority_when_budget_is_tight(db, project, actor, monkeypatch):
    from app.services.token_budget import estimate_tokens

    upstream = make_node(db, project, node_key="upstream", order_index=0, output_artifact_type="upstream_doc")
    node = make_node(
        db, project, node_key="node_a", order_index=1, status=WorkflowStatus.READY, required_inputs=["upstream_doc"]
    )
    artifact_content = "A short required artifact summary."
    make_approved_artifact(db, project, upstream, actor, content=artifact_content)
    prompt = make_agent_prompt(db, stage="node_a")
    monkeypatch.setattr(
        "app.services.context_builder.retrieve_relevant_chunks",
        lambda *a, **k: [_chunk("Big Source", " ".join(["word"] * 200), 0.9)],
    )
    _add_review_comments(db, node, actor, ["A review comment that should be dropped under a tight budget."])

    # Exactly enough for stage_rules + project_summary + the artifact
    # summary, with nothing left over for anything lower-priority —
    # unlike a loose "small" budget, this leaves no leftover room for a
    # smaller P3/P4 block to sneak in after a bigger one is skipped.
    node.context_token_budget = (
        estimate_tokens(_stage_rules_text(node))
        + estimate_tokens(_project_summary_text(project))
        + estimate_tokens(artifact_content)
    )
    db.flush()

    result = ContextBuilderService(db).build(project_id=project.id, node_id=node.id, active_prompt=prompt)

    # Higher-priority sections survive...
    assert result.stage_rules
    assert result.project_summary
    assert result.required_artifact_summaries.get("upstream_doc")
    # ...while the lowest-priority ones were dropped to make room.
    assert result.rag_context == []
    assert result.human_comments == []
    assert result.token_budget_report["over_budget"] is True


def test_rag_context_kept_over_human_comments_when_only_one_fits(db, project, actor, monkeypatch):
    """P3 (RAG) outranks P4 (comments) — the loop processes RAG chunks
    first, so with just enough room for one of the two, it's the RAG
    chunk that's included and the comment that's cut."""
    from app.services.token_budget import estimate_tokens

    node, prompt = _setup(db, project)
    small_chunk_content = "A small relevant excerpt."
    rag_block_content = f"## Source: Small Source\n{small_chunk_content}"
    monkeypatch.setattr(
        "app.services.context_builder.retrieve_relevant_chunks",
        lambda *a, **k: [_chunk("Small Source", small_chunk_content, 0.9)],
    )
    _add_review_comments(db, node, actor, ["A comment that loses out to the RAG chunk."])

    # Just enough for stage_rules + project_summary + the RAG block (with
    # its "## Source: ..." framing), not enough left over for the comment.
    node.context_token_budget = (
        estimate_tokens(_stage_rules_text(node))
        + estimate_tokens(_project_summary_text(project))
        + estimate_tokens(rag_block_content)
    )
    db.flush()

    result = ContextBuilderService(db).build(project_id=project.id, node_id=node.id, active_prompt=prompt)

    assert len(result.rag_context) == 1
    assert result.rag_context[0]["source_title"] == "Small Source"
    assert result.human_comments == []


def test_stage_rules_is_truncated_rather_than_dropped_under_an_extreme_budget(db, project, monkeypatch):
    node, prompt = _setup(db, project)
    monkeypatch.setattr("app.services.context_builder.retrieve_relevant_chunks", lambda *a, **k: [])

    node.context_token_budget = 1  # far too small for anything to fit whole
    db.flush()

    result = ContextBuilderService(db).build(project_id=project.id, node_id=node.id, active_prompt=prompt)

    # Never dropped entirely — compressible blocks always keep at least
    # MIN_COMPRESSED_TOKENS worth of content (see token_budget.py).
    assert result.stage_rules != ""
    assert "truncated to fit" in result.stage_rules or len(result.stage_rules) < 200
    assert "stage_rules" in result.token_budget_report["blocks"][0]["label"]
