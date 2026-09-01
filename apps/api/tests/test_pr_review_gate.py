"""Graph-rule tests for the workflow extension: LLD -> Implementation ->
PR Review -> Testing (see workflows/sdlc-workflow.json) and the Testing
evidence gate (see app/services/graph_engine.py's
validate_evidence_requirement).

Follows test_graph_engine.py's exact conventions: plain functions, state
built via tests/conftest.py's make_node/make_edge/make_approved_artifact,
the db/project/actor fixtures, a fresh GraphEngineService(db) per test.
"""

from app.models import ArtifactVersion, WorkflowStatus
from app.services.graph_engine import GraphEngineService
from tests.conftest import make_approved_artifact, make_node


# --- Rule 6: Implementation cannot start before LLD approval -----------------------


def test_implementation_cannot_run_before_lld_is_approved(db, project, actor):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    implementation = make_node(
        db,
        project,
        node_key="implementation",
        order_index=1,
        status=WorkflowStatus.READY,
        required_inputs=["lld_document"],
        output_artifact_type="code_change",
        requires_human_approval=False,
    )
    del lld  # only its output_artifact_type matters for resolve_required_inputs

    engine = GraphEngineService(db)
    result = engine.validate_can_run(project=project, node=implementation, freeform_context={})

    assert result.can_run is False
    assert any("lld_document" in reason for reason in result.reasons)


def test_implementation_can_run_once_lld_is_approved(db, project, actor):
    lld = make_node(db, project, node_key="lld", order_index=0, output_artifact_type="lld_document")
    implementation = make_node(
        db,
        project,
        node_key="implementation",
        order_index=1,
        status=WorkflowStatus.READY,
        required_inputs=["lld_document"],
        output_artifact_type="code_change",
        requires_human_approval=False,
    )
    make_approved_artifact(db, project, lld, actor, content="## Component Design\n\nDone.")

    result = GraphEngineService(db).validate_can_run(project=project, node=implementation, freeform_context={})

    assert result.can_run is True
    assert "lld_document" in result.approved_artifact_content


# --- Rule 7: PR review cannot start before a PR/code diff (code_change) exists -----


def test_pr_review_cannot_run_before_code_change_is_approved(db, project, actor):
    implementation = make_node(
        db, project, node_key="implementation", order_index=0, output_artifact_type="code_change"
    )
    pr_review = make_node(
        db,
        project,
        node_key="pr_review",
        order_index=1,
        status=WorkflowStatus.READY,
        required_inputs=["code_change"],
        output_artifact_type="pr_review_report",
    )
    del implementation

    result = GraphEngineService(db).validate_can_run(project=project, node=pr_review, freeform_context={})

    assert result.can_run is False
    assert any("code_change" in reason for reason in result.reasons)


def test_pr_review_can_run_once_code_change_is_approved(db, project, actor):
    implementation = make_node(
        db, project, node_key="implementation", order_index=0, output_artifact_type="code_change"
    )
    pr_review = make_node(
        db,
        project,
        node_key="pr_review",
        order_index=1,
        status=WorkflowStatus.READY,
        required_inputs=["code_change"],
        output_artifact_type="pr_review_report",
    )
    # This is the state app/api/routes/agent_runs.py's auto-finalize path
    # (a VALIDATE run on a requires_human_approval=False node) or
    # artifacts.py's submit_artifact_for_review now puts Implementation's
    # artifact into — see both routes' new "stage does not require human
    # approval" branch.
    make_approved_artifact(db, project, implementation, actor, content="Added the login endpoint.")

    result = GraphEngineService(db).validate_can_run(project=project, node=pr_review, freeform_context={})

    assert result.can_run is True
    assert "code_change" in result.approved_artifact_content


# --- Rule 8: Testing cannot be marked complete (approved) without test evidence ----


def _version(content_markdown: str) -> ArtifactVersion:
    # Not persisted — validate_evidence_requirement only reads
    # content_markdown, so a bare in-memory instance is enough.
    return ArtifactVersion(content_markdown=content_markdown)


def test_approve_review_rejects_missing_test_evidence_section(db, project, actor):
    testing = make_node(
        db, project, node_key="testing", order_index=0, required_evidence_section="Test Evidence"
    )
    version = _version("## Summary\n\nAll acceptance criteria pass.\n")

    error = GraphEngineService(db).validate_evidence_requirement(testing, version)

    assert error is not None
    assert "Test Evidence" in error


def test_approve_review_rejects_empty_test_evidence_section(db, project, actor):
    testing = make_node(
        db, project, node_key="testing", order_index=0, required_evidence_section="Test Evidence"
    )
    version = _version("## Summary\n\nAll good.\n\n## Test Evidence\n\n   \n\n## Defects Found\n\nNone.\n")

    error = GraphEngineService(db).validate_evidence_requirement(testing, version)

    assert error is not None
    assert "empty" in error.lower()


def test_approve_review_succeeds_with_test_evidence_section(db, project, actor):
    testing = make_node(
        db, project, node_key="testing", order_index=0, required_evidence_section="Test Evidence"
    )
    version = _version(
        "## Summary\n\nAll good.\n\n"
        "## Test Evidence\n\n42/42 tests passed. CI run: https://ci.example.com/run/123\n\n"
        "## Defects Found\n\nNone.\n"
    )

    error = GraphEngineService(db).validate_evidence_requirement(testing, version)

    assert error is None


def test_approve_review_ignores_evidence_requirement_when_node_has_none(db, project, actor):
    hld = make_node(db, project, node_key="hld", order_index=0)  # required_evidence_section defaults to None
    version = _version("## Overview\n\nSomething.\n")

    error = GraphEngineService(db).validate_evidence_requirement(hld, version)

    assert error is None


def test_evidence_section_lookup_is_case_insensitive(db, project, actor):
    testing = make_node(
        db, project, node_key="testing", order_index=0, required_evidence_section="Test Evidence"
    )
    version = _version("## test evidence\n\n10/10 passed.\n")

    error = GraphEngineService(db).validate_evidence_requirement(testing, version)

    assert error is None
