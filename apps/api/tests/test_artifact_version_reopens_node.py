"""Regression test: creating a brand-new artifact version (the "Create new
version" button in apps/web/components/documents/artifact-editor.tsx) used
to reset only the Artifact's own status back to DRAFT, never the owning
WorkflowNode's — so a node that had already reached APPROVED (the normal
end state of a completed review) stayed stuck there forever after, and
"Run Agent" kept refusing with "node status is APPROVED; must be one of:
FAILED, NEEDS_CHANGES, READY, WAITING_FOR_INPUT". The only documented way
out, POST /reviews/{id}/request-changes, itself refuses once that review
was already decided — a closed door once approved, with no other path
back to a runnable state. See app/api/routes/artifacts.py's
create_artifact_version for the fix.
"""

import pytest

import app.api.routes.agent_runs as agent_runs
from app.api.routes.agent_runs import start_agent_run
from app.api.routes.artifacts import create_artifact_version
from app.models import AgentPromptRole, WorkflowStatus
from app.schemas.agent_run import AgentRunCreate
from app.schemas.artifact import ArtifactVersionCreate
from app.services import ai_generation
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")
    monkeypatch.setattr(agent_runs, "retrieve_relevant_chunks", lambda *a, **kw: [])


def test_create_artifact_version_reopens_an_approved_node(db, project, actor):
    node = make_node(db, project, node_key="node_a", order_index=0, output_artifact_type="doc_a")
    make_agent_prompt(db, stage="node_a")
    artifact = make_approved_artifact(db, project, node, actor, content="Original content.")
    node.status = WorkflowStatus.APPROVED  # the real end state a completed review leaves behind
    db.flush()

    # Confirms the bug: before the fix, "Run Agent" fails here — a real,
    # recorded AgentRun(status=FAILED), not a bare HTTP rejection (see
    # agent_runs.py's own _fail helper).
    failed_run = start_agent_run(AgentRunCreate(project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.DRAFT, triggered_by_user_id=actor.id), db)
    assert failed_run.status.value == "FAILED"
    assert "node status is APPROVED" in failed_run.error_message

    create_artifact_version(
        artifact.id, ArtifactVersionCreate(content_markdown="Edited content.", created_by_id=actor.id), db,
    )

    db.refresh(node)
    assert node.status == WorkflowStatus.NEEDS_CHANGES

    # The real fix: "Run Agent" now works again instead of failing.
    run = start_agent_run(AgentRunCreate(project_id=project.id, workflow_node_id=node.id, action=AgentPromptRole.DRAFT, triggered_by_user_id=actor.id), db)
    assert run.status.value != "FAILED"


def test_create_artifact_version_leaves_an_already_runnable_node_alone(db, project, actor):
    """No spurious status churn for the common case — a node already
    READY/NEEDS_CHANGES/WAITING_FOR_INPUT/FAILED shouldn't be touched by
    this at all."""
    node = make_node(db, project, node_key="node_b", order_index=0, output_artifact_type="doc_b", status=WorkflowStatus.READY)
    make_agent_prompt(db, stage="node_b")
    artifact = make_approved_artifact(db, project, node, actor, content="Original content.")
    node.status = WorkflowStatus.READY
    db.flush()

    create_artifact_version(
        artifact.id, ArtifactVersionCreate(content_markdown="Edited content.", created_by_id=actor.id), db,
    )

    db.refresh(node)
    assert node.status == WorkflowStatus.READY
