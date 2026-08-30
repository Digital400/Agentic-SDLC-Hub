"""Shared pytest fixtures for the backend test suite.

Uses an in-memory SQLite database rather than the real Postgres instance —
these tests exercise GraphEngineService's pure graph logic, not anything
Postgres-specific (no pgvector, no native enums; every status column here
is `native_enum=False`, i.e. a plain VARCHAR, so it round-trips through
SQLite fine).

`Base.metadata.create_all` is given an explicit `tables=[...]` list rather
than being called bare, because `KnowledgeChunk` has a pgvector `Vector`
column whose DDL SQLite can't compile — none of these tests touch
knowledge chunks, so that table is simply left out of the schema.
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    AgentDefinition,
    AgentPrompt,
    AgentRun,
    AgentRunLoopEvent,
    Artifact,
    ArtifactVersion,
    AuditLog,
    Base,
    Project,
    ProjectMember,
    Review,
    ReviewComment,
    User,
    UserRole,
    WorkflowEdge,
    WorkflowNode,
)
from app.models.enums import AgentPromptRole, ArtifactStatus, ProjectStatus, WorkflowStatus

TEST_TABLES = [
    User.__table__,
    Project.__table__,
    ProjectMember.__table__,
    WorkflowNode.__table__,
    WorkflowEdge.__table__,
    Artifact.__table__,
    ArtifactVersion.__table__,
    Review.__table__,
    ReviewComment.__table__,
    AuditLog.__table__,
    AgentDefinition.__table__,
    AgentPrompt.__table__,
    AgentRun.__table__,
    AgentRunLoopEvent.__table__,
]


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def actor(db: Session) -> User:
    """A generic Admin user to attribute audited actions to — the specific
    role doesn't matter to GraphEngineService itself (permissions.py is a
    separate layer checked by the route handlers, not by the engine)."""
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Test Actor", role=UserRole.ADMIN)
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def project(db: Session, actor: User) -> Project:
    proj = Project(
        name="Test Project",
        business_owner="Test Business Owner",
        workflow_template_id="sdlc-workflow",
        workflow_template_version="test",
        current_stage="node_a",
        status=ProjectStatus.ACTIVE,
        created_by_id=actor.id,
    )
    db.add(proj)
    db.flush()
    return proj


def make_node(
    db: Session,
    project: Project,
    *,
    node_key: str,
    order_index: int,
    status: WorkflowStatus = WorkflowStatus.LOCKED,
    required_inputs: list[str] | None = None,
    output_artifact_type: str | None = None,
) -> WorkflowNode:
    """Helper (not a fixture, since tests need several nodes with different
    keys) for building one workflow node with sensible defaults."""
    node = WorkflowNode(
        project_id=project.id,
        node_key=node_key,
        name=node_key.replace("_", " ").title(),
        description=f"{node_key} stage",
        agent_key=f"{node_key}_agent",
        required_inputs=required_inputs or [],
        output_artifact_type=output_artifact_type or f"{node_key}_doc",
        requires_human_approval=True,
        allowed_actions=["DRAFT", "IMPROVE", "VALIDATE"],
        status=status,
        order_index=order_index,
    )
    db.add(node)
    db.flush()
    return node


def make_edge(
    db: Session, project: Project, source: WorkflowNode, target: WorkflowNode, *, label: str | None = None
) -> WorkflowEdge:
    edge = WorkflowEdge(project_id=project.id, source_node_id=source.id, target_node_id=target.id, label=label)
    db.add(edge)
    db.flush()
    db.refresh(source)
    db.refresh(target)
    return edge


def make_approved_artifact(
    db: Session, project: Project, node: WorkflowNode, actor: User, *, content: str = "Approved content"
) -> Artifact:
    """An APPROVED artifact whose type matches `node.output_artifact_type`
    — the shape `resolve_required_inputs` looks for when a downstream
    node lists this node's output as a required input."""
    artifact = Artifact(
        project_id=project.id,
        workflow_node_id=node.id,
        artifact_type=node.output_artifact_type,
        title=f"{node.name} artifact",
        status=ArtifactStatus.APPROVED,
        created_by_id=actor.id,
    )
    db.add(artifact)
    db.flush()

    version = ArtifactVersion(
        artifact_id=artifact.id, version_number=1, content_markdown=content, created_by_id=actor.id
    )
    db.add(version)
    db.flush()

    artifact.current_version_id = version.id
    db.flush()
    db.refresh(artifact)
    return artifact


def make_agent_prompt(
    db: Session, *, stage: str = "node_a", checklist: list[str] | None = None
) -> AgentPrompt:
    """An AgentDefinition + its active DRAFT prompt — the pair
    LoopEngineService.run_loop needs as `active_prompt`."""
    agent = AgentDefinition(agent_key=f"{stage}_agent", name=f"{stage} Agent", model_name="mock")
    db.add(agent)
    db.flush()

    prompt = AgentPrompt(
        agent_definition_id=agent.id,
        role=AgentPromptRole.DRAFT,
        version=1,
        name=f"{stage} draft prompt",
        stage=stage,
        system_prompt="Draft the stage output.",
        output_format="Markdown.",
        validation_checklist=checklist or [],
        is_active=True,
    )
    db.add(prompt)
    db.flush()
    db.refresh(prompt)
    return prompt


def make_agent_run(db: Session, project: Project, node: WorkflowNode, prompt: AgentPrompt) -> AgentRun:
    run = AgentRun(
        project_id=project.id,
        workflow_node_id=node.id,
        agent_definition_id=prompt.agent_definition_id,
        agent_prompt_id=prompt.id,
        action=AgentPromptRole.DRAFT,
    )
    db.add(run)
    db.flush()
    return run
