"""Unit tests for ai_generation.build_prioritized_context's summary-vs-full
-content rule — see its docstring. Covers the "Context Builder must use
agentContextSummary by default" requirement and its three exceptions.
"""

from app.services.ai_generation import build_prioritized_context
from tests.conftest import make_node


def _labels(result, priority=None):
    return [b.label for b in result.blocks if priority is None or b.priority == priority]


def test_approved_input_uses_summary_by_default(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0)

    result = build_prioritized_context(
        project=project,
        node=node,
        approved_artifact_content={"hld_document": "full HLD text " * 50},
        approved_artifact_summaries={"hld_document": "Condensed HLD summary."},
        freeform_context={},
        context_token_budget=8000,
        output_token_budget=2048,
    )

    assert "summary:hld_document" in _labels(result, "P2")
    assert "full:hld_document" not in _labels(result, "P5")
    assert "Condensed HLD summary." in result.assembled_text()
    assert "full HLD text" not in result.assembled_text()


def test_node_full_content_artifact_types_escalates_that_artifact_to_full(db, project):
    node = make_node(db, project, node_key="node_a", order_index=0)

    result = build_prioritized_context(
        project=project,
        node=node,
        approved_artifact_content={"hld_document": "full HLD text", "story_backlog": "full stories text"},
        approved_artifact_summaries={"hld_document": "HLD summary.", "story_backlog": "Stories summary."},
        freeform_context={},
        context_token_budget=8000,
        output_token_budget=2048,
        full_content_artifact_types={"hld_document"},
    )

    # Only the configured artifact_type escalates — "only if required" is
    # decided per artifact, not all-or-nothing for the run.
    assert "full:hld_document" in _labels(result, "P5")
    assert "summary:story_backlog" in _labels(result, "P2")
    assert "full HLD text" in result.assembled_text()
    assert "full stories text" not in result.assembled_text()


def test_force_full_content_escalates_every_approved_input(db, project):
    """Models generate()'s action==IMPROVE/VALIDATE rule: a human-requested
    revision or a validator's full-document check needs everything in
    full, not just the stage-configured artifact_type(s)."""
    node = make_node(db, project, node_key="node_a", order_index=0)

    result = build_prioritized_context(
        project=project,
        node=node,
        approved_artifact_content={"hld_document": "full HLD text", "story_backlog": "full stories text"},
        approved_artifact_summaries={"hld_document": "HLD summary.", "story_backlog": "Stories summary."},
        freeform_context={},
        context_token_budget=8000,
        output_token_budget=2048,
        force_full_content=True,
    )

    assert set(_labels(result, "P5")) == {"full:hld_document", "full:story_backlog"}
    assert _labels(result, "P2") == []


def test_missing_summary_falls_back_to_truncated_full_content_at_p2():
    """An artifact approved before app/services/artifact_summary.py existed
    (or whose summarization never ran) has no agent_context_summary — the
    context builder must still work, just via a plain-truncation fallback,
    and it must stay at P2 (not silently escalate to P5)."""
    from app.models import Project, WorkflowNode

    project = Project(name="P", business_owner="B", workflow_template_id="t", workflow_template_version="1", current_stage="node_a")
    node = WorkflowNode(
        node_key="node_a", name="Node A", description="desc", agent_key="agent_a",
        required_inputs=[], output_artifact_type="doc_a", requires_human_approval=True,
        allowed_actions=[], order_index=0,
    )

    result = build_prioritized_context(
        project=project,
        node=node,
        approved_artifact_content={"intake_summary": "x" * 2000},
        approved_artifact_summaries={},  # nothing summarized yet
        freeform_context={},
        context_token_budget=8000,
        output_token_budget=2048,
    )

    assert "summary:intake_summary" in _labels(result, "P2")
    assert "not yet summarized" in result.assembled_text()
