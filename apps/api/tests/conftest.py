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
    ImplementationRun,
    ImplementationTask,
    ImplementationTaskArea,
    ImplementationTaskRiskLevel,
    ImplementationTaskStatus,
    Integration,
    PullRequestLink,
    TestRun,
    PRReviewRun,
    MaintenanceRun,
    JiraProjectLink,
    JiraIssueLink,
    ConfluenceSpaceLink,
    ConfluencePageLink,
    IntegrationConnection,
    Project,
    ProjectMember,
    Repository,
    RepositoryFileIndex,
    RepositorySnapshot,
    Review,
    ReviewComment,
    Sprint,
    Story,
    StoryActivityLog,
    StoryArtifact,
    StoryAssignee,
    StoryDeliveryEdge,
    StoryDeliveryLane,
    StoryDeliveryNode,
    SprintStory,
    User,
    UserRole,
    ValidatorDefinition,
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
    Story.__table__,
    StoryAssignee.__table__,
    StoryDeliveryLane.__table__,
    StoryDeliveryNode.__table__,
    StoryDeliveryEdge.__table__,
    StoryArtifact.__table__,
    StoryActivityLog.__table__,
    Sprint.__table__,
    SprintStory.__table__,
    Review.__table__,
    ReviewComment.__table__,
    AuditLog.__table__,
    AgentDefinition.__table__,
    AgentPrompt.__table__,
    AgentRun.__table__,
    AgentRunLoopEvent.__table__,
    ValidatorDefinition.__table__,
    ImplementationTask.__table__,
    ImplementationRun.__table__,
    PullRequestLink.__table__,
    TestRun.__table__,
    PRReviewRun.__table__,
    MaintenanceRun.__table__,
    JiraProjectLink.__table__,
    JiraIssueLink.__table__,
    ConfluenceSpaceLink.__table__,
    ConfluencePageLink.__table__,
    Integration.__table__,
    IntegrationConnection.__table__,
    Repository.__table__,
    RepositorySnapshot.__table__,
    RepositoryFileIndex.__table__,
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
    requires_human_approval: bool = True,
    required_evidence_section: str | None = None,
    story_id: "uuid.UUID | None" = None,
) -> WorkflowNode:
    """Helper (not a fixture, since tests need several nodes with different
    keys) for building one workflow node with sensible defaults.

    `story_id` scopes this node to one Scrum story lane (see
    app/models/workflow.py's `story_id` column) — omitted, it stays a
    project-level node exactly as before."""
    node = WorkflowNode(
        project_id=project.id,
        story_id=story_id,
        node_key=node_key,
        name=node_key.replace("_", " ").title(),
        description=f"{node_key} stage",
        agent_key=f"{node_key}_agent",
        required_inputs=required_inputs or [],
        output_artifact_type=output_artifact_type or f"{node_key}_doc",
        requires_human_approval=requires_human_approval,
        required_evidence_section=required_evidence_section,
        allowed_actions=["DRAFT", "IMPROVE", "VALIDATE"],
        status=status,
        order_index=order_index,
    )
    db.add(node)
    db.flush()
    return node


def make_story(
    db: Session,
    project: Project,
    actor: User,
    source_artifact_version_id: "uuid.UUID",
    *,
    title: str = "Sample Story",
    story_type: "StoryType" = None,
) -> "Story":
    from app.models import Story, StoryType as _StoryType

    story = Story(
        project_id=project.id,
        source_artifact_version_id=source_artifact_version_id,
        story_type=story_type or _StoryType.VERTICAL,
        title=title,
        user_story=f"As a user, I want {title.lower()}.",
        created_by_id=actor.id,
    )
    db.add(story)
    db.flush()
    return story


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
    db: Session, project: Project, node: WorkflowNode, actor: User, *, content: str = "Approved content",
    story_id: "uuid.UUID | None" = None,
) -> Artifact:
    """An APPROVED artifact whose type matches `node.output_artifact_type`
    — the shape `resolve_required_inputs` looks for when a downstream
    node lists this node's output as a required input.

    `story_id` defaults to `node.story_id` (so a lane node's own artifact
    is scoped to the same lane by default) — pass it explicitly only to
    build a mismatched-scope artifact on purpose."""
    artifact = Artifact(
        project_id=project.id,
        workflow_node_id=node.id,
        story_id=node.story_id if story_id is None else story_id,
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


def make_implementation_task(
    db: Session,
    project: Project,
    node: WorkflowNode,
    artifact: Artifact,
    *,
    title: str = "Add password reset endpoint",
    description: str = "Add a POST /auth/password-reset endpoint.",
    linked_story: str | None = None,
    linked_lld_section: str | None = None,
    area: ImplementationTaskArea = ImplementationTaskArea.BACKEND,
    expected_paths: list[str] | None = None,
    dependencies: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    test_expectation: str = "",
    risk_level: ImplementationTaskRiskLevel = ImplementationTaskRiskLevel.MEDIUM,
    assigned_agent_type: str = "backend-coding-agent",
    status: ImplementationTaskStatus = ImplementationTaskStatus.PENDING,
    order_index: int = 0,
    story_id: "uuid.UUID | None" = None,
) -> ImplementationTask:
    """One ImplementationTask row, tied to `artifact`'s current version —
    mirrors what app/api/routes/projects.py's generate_implementation_plan
    actually persists, for tests that need a task without going through
    that whole endpoint. `story_id` defaults to `node.story_id`, same
    convention as make_approved_artifact above."""
    task = ImplementationTask(
        project_id=project.id,
        workflow_node_id=node.id,
        artifact_id=artifact.id,
        artifact_version_id=artifact.current_version_id,
        story_id=node.story_id if story_id is None else story_id,
        title=title,
        description=description,
        linked_story=linked_story,
        linked_lld_section=linked_lld_section,
        area=area,
        expected_paths=expected_paths or [],
        dependencies=dependencies or [],
        acceptance_criteria=acceptance_criteria or [],
        test_expectation=test_expectation,
        risk_level=risk_level,
        assigned_agent_type=assigned_agent_type,
        status=status,
        order_index=order_index,
    )
    db.add(task)
    db.flush()
    db.refresh(task)
    return task


def make_agent_prompt(
    db: Session,
    *,
    stage: str = "node_a",
    checklist: list[str] | None = None,
    role: AgentPromptRole = AgentPromptRole.DRAFT,
    agent: AgentDefinition | None = None,
) -> AgentPrompt:
    """An AgentDefinition (unless one is passed in, e.g. to add a second
    role's prompt to the same agent) + its active prompt for `role` — the
    pair LoopEngineService.run_loop / revision_agent.run_revision_agent
    need as `active_prompt`."""
    if agent is None:
        agent = AgentDefinition(agent_key=f"{stage}_agent", name=f"{stage} Agent", model_name="mock")
        db.add(agent)
        db.flush()

    prompt = AgentPrompt(
        agent_definition_id=agent.id,
        role=role,
        version=1,
        name=f"{stage} {role.value} prompt",
        stage=stage,
        system_prompt=f"{role.value.title()} the stage output.",
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


def make_validator_definition(
    db: Session, *, stage: str = "node_a", criteria: list[str] | None = None, quality_threshold: float = 0.8
) -> ValidatorDefinition:
    validator = ValidatorDefinition(
        validator_key=f"{stage}-validator",
        name=f"{stage} Validator",
        stage=stage,
        model_name="mock",
        quality_threshold=quality_threshold,
        criteria=criteria or [],
    )
    db.add(validator)
    db.flush()
    return validator
