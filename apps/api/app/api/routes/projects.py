"""Project management endpoints.

Covers: create, list, get, update, archive a project; list a project's
workflow nodes; update one node's status. See docs/mvp-plan.md for the
user journey these support.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    AgentRun,
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    ImplementationRun,
    ImplementationTask,
    ImplementationTaskArea,
    ImplementationTaskRiskLevel,
    JiraProjectLink,
    ConfluenceSpaceLink,
    MaintenanceRun,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
    PullRequestLink,
    Repository,
    RepositorySnapshot,
    PRReviewRun,
    Review,
    TestRun,
    ReviewStatus,
    User,
    WorkflowEdge,
    WorkflowNode,
    WorkflowStatus,
    WorkType,
)
from app.schemas.agent_run import AgentRunRead
from app.schemas.artifact import ArtifactRead
from app.schemas.github_integration import RepositoryRead
from app.schemas.implementation_task import (
    GenerateImplementationPlanRequest,
    GenerateImplementationPlanResponse,
    ImplementationTaskRead,
)
from app.schemas.implementation_run import ImplementationRunRead
from app.schemas.test_run import TestRunRead
from app.schemas.pr_review_run import PRReviewRunRead
from app.schemas.jira_export import JiraExportPreviewRead
from app.schemas.jira_integration import JiraProjectLinkRead
from app.schemas.confluence_integration import ConfluenceSpaceLinkRead
from app.schemas.maintenance_run import MaintenanceRunRead
from app.schemas.project import ProjectCreate, ProjectListResponse, ProjectRead, ProjectUpdate
from app.schemas.repo_context import RepoContextPreviewRead
from app.schemas.review import ReviewRead
from app.schemas.workflow import WorkflowEdgeRead, WorkflowNodeRead, WorkflowNodeStatusUpdate
from app.api.routes.github_integration import decrypt_repository_token
from app.api.routes.test_runs import get_review_id_for_test_run
from app.services.audit import record_audit_log
from app.services.github_integration import GitHubIntegrationError
from app.services.graph_engine import GraphEngineService
from app.services.implementation_planner import build_implementation_plan, render_plan_markdown
from app.services.jira_export import PUSH_TO_JIRA_ENABLED, build_jira_export_preview
from app.services.permissions import require_can_edit_stage, require_can_override_node, require_can_update_project
from app.services.repo_context_builder import DEFAULT_MAX_CONTEXT_TOKENS, DEFAULT_MAX_FILE_COUNT, RepoContextBuilderService
from app.services.story_export import STORY_BACKLOG_ARTIFACT_TYPE, parse_story_backlog
from app.services.workflow_templates import WorkflowTemplateError, generate_workflow_graph, load_workflow_template

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


# 1. Create project ----------------------------------------------------------


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    """Create a project and generate its workflow graph from a template.

    The first node (the template's startNode — "requirement_intake" in the
    default workflow) is set IN_PROGRESS; every other node starts
    NOT_STARTED. `name` and `business_owner` are required by the request
    schema itself (empty strings are rejected).
    """
    creator = db.get(User, payload.created_by_id)
    if creator is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"created_by_id {payload.created_by_id} does not match an existing user")

    # work_type picks the template only when the caller didn't already
    # name one explicitly (workflow_template_file) — e.g. the Scrum Story
    # Lanes template is opt-in and only ever reachable that way, unrelated
    # to work_type. NEW_PROJECT keeps today's default (sdlc-workflow.json,
    # via load_workflow_template(None)); the other three work types all
    # use the existing-project feature template — see WorkType's own
    # docstring for why they share one.
    template_file = payload.workflow_template_file
    if template_file is None and payload.work_type != WorkType.NEW_PROJECT:
        template_file = "existing-project-feature-workflow.json"

    try:
        template = load_workflow_template(template_file)
    except WorkflowTemplateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    project = Project(
        name=payload.name,
        description=payload.description,
        business_owner=payload.business_owner,
        work_type=payload.work_type,
        workflow_template_id=template["id"],
        workflow_template_version=template["version"],
        current_stage=template["startNode"],
        status=ProjectStatus.ACTIVE,
        created_by=creator,
    )
    db.add(project)
    db.flush()

    db.add(ProjectMember(project=project, user=creator, role=ProjectRole.OWNER))
    generate_workflow_graph(db, project, template)

    record_audit_log(
        db,
        project_id=project.id,
        actor_user_id=creator.id,
        action="project.created",
        entity_type="Project",
        entity_id=project.id,
        extra_data={"workflow_template_id": template["id"], "workflow_template_version": template["version"]},
    )

    db.commit()
    db.refresh(project)
    return project


# 2. List projects ------------------------------------------------------------


@router.get("", response_model=ProjectListResponse)
def list_projects(
    db: Session = Depends(get_db),
    project_status: ProjectStatus | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> ProjectListResponse:
    query = db.query(Project)
    if project_status is not None:
        query = query.filter(Project.status == project_status)

    total = query.count()
    items = query.order_by(Project.created_at.desc()).offset(skip).limit(limit).all()
    return ProjectListResponse(items=items, total=total)


# 3. Get project by id ---------------------------------------------------------


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    return _get_project_or_404(db, project_id)


# 4. Update project -------------------------------------------------------------


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: uuid.UUID, payload: ProjectUpdate, db: Session = Depends(get_db)) -> Project:
    project = _get_project_or_404(db, project_id)

    actor = db.get(User, payload.updated_by_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"updated_by_id {payload.updated_by_id} does not match an existing user")
    require_can_update_project(actor)

    if project.status == ProjectStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot update an archived project")

    changes: dict[str, object] = {}

    if payload.name is not None:
        changes["name"] = (project.name, payload.name)
        project.name = payload.name

    if payload.business_owner is not None:
        changes["business_owner"] = (project.business_owner, payload.business_owner)
        project.business_owner = payload.business_owner

    if payload.description is not None:
        changes["description"] = (project.description, payload.description)
        project.description = payload.description

    if payload.current_stage is not None:
        node_exists = (
            db.query(WorkflowNode)
            .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == payload.current_stage)
            .first()
        )
        if node_exists is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"current_stage '{payload.current_stage}' does not match any workflow node of this project",
            )
        changes["current_stage"] = (project.current_stage, payload.current_stage)
        project.current_stage = payload.current_stage

    if changes:
        record_audit_log(
            db,
            project_id=project.id,
            action="project.updated",
            entity_type="Project",
            entity_id=project.id,
            extra_data={field: {"from": old, "to": new} for field, (old, new) in changes.items()},
        )

    db.commit()
    db.refresh(project)
    return project


# 5. Archive project -------------------------------------------------------------


@router.post("/{project_id}/archive", response_model=ProjectRead)
def archive_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    project = _get_project_or_404(db, project_id)

    if project.status == ProjectStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Project is already archived")

    previous_status = project.status
    project.status = ProjectStatus.ARCHIVED

    record_audit_log(
        db,
        project_id=project.id,
        action="project.archived",
        entity_type="Project",
        entity_id=project.id,
        extra_data={"from": previous_status.value, "to": ProjectStatus.ARCHIVED.value},
    )

    db.commit()
    db.refresh(project)
    return project


# 6. Get project workflow nodes ---------------------------------------------------


@router.get("/{project_id}/workflow-nodes", response_model=list[WorkflowNodeRead])
def list_project_workflow_nodes(
    project_id: uuid.UUID, story_id: uuid.UUID | None = None, db: Session = Depends(get_db)
) -> list[WorkflowNode]:
    """`story_id` narrows this to one Scrum story lane's own nodes (see
    app/models/workflow.py's `story_id` column) — the same
    WorkflowCanvas/NodeDetailsPanel components render either the whole
    project graph (omitted, the default — every existing project-level
    node has `story_id IS NULL`) or one lane, unmodified. Omitting it
    keeps today's behavior exactly as it was."""
    _get_project_or_404(db, project_id)
    query = db.query(WorkflowNode).filter(WorkflowNode.project_id == project_id)
    query = query.filter(WorkflowNode.story_id == story_id) if story_id is not None else query.filter(WorkflowNode.story_id.is_(None))
    return query.order_by(WorkflowNode.order_index).all()


# Get project workflow edges ---------------------------------------------------


@router.get("/{project_id}/workflow-edges", response_model=list[WorkflowEdgeRead])
def list_project_workflow_edges(
    project_id: uuid.UUID, story_id: uuid.UUID | None = None, db: Session = Depends(get_db)
) -> list[WorkflowEdge]:
    """Needed alongside workflow-nodes to render the graph — an edge list
    with no nodes (or vice versa) can't be drawn. `story_id` scopes this
    the same way list_project_workflow_nodes does."""
    _get_project_or_404(db, project_id)
    query = db.query(WorkflowEdge).filter(WorkflowEdge.project_id == project_id)
    query = query.filter(WorkflowEdge.story_id == story_id) if story_id is not None else query.filter(WorkflowEdge.story_id.is_(None))
    return query.all()


# List artifacts by project ---------------------------------------------------


@router.get("/{project_id}/artifacts", response_model=list[ArtifactRead])
def list_project_artifacts(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ArtifactRead]:
    _get_project_or_404(db, project_id)
    artifacts = (
        db.query(Artifact)
        .filter(Artifact.project_id == project_id)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    return [ArtifactRead.from_orm_artifact(a) for a in artifacts]


# 3. List agent runs by project -----------------------------------------------------


@router.get("/{project_id}/agent-runs", response_model=list[AgentRunRead])
def list_project_agent_runs(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[AgentRunRead]:
    _get_project_or_404(db, project_id)
    runs = db.query(AgentRun).filter(AgentRun.project_id == project_id).order_by(AgentRun.created_at.desc()).all()
    return [AgentRunRead.from_orm_run(r) for r in runs]


# 4. List implementation tasks by project --------------------------------------------


@router.get("/{project_id}/implementation-tasks", response_model=list[ImplementationTaskRead])
def list_project_implementation_tasks(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ImplementationTask]:
    _get_project_or_404(db, project_id)
    return (
        db.query(ImplementationTask)
        .filter(ImplementationTask.project_id == project_id)
        .order_by(ImplementationTask.order_index)
        .all()
    )


# 5. Get a project's configured GitHub repository, if any ---------------------------


@router.get("/{project_id}/github-repository", response_model=RepositoryRead | None)
def get_project_github_repository(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Repository | None:
    """The project's PRIMARY repository (see Repository.is_primary) — for
    a project with multiple connected repositories, use
    GET /{project_id}/github-repositories instead to see all of them. See
    app/api/routes/github_integration.py for the read-only GitHub actions
    (branches, tree, file, snapshots) against the repository this returns."""
    _get_project_or_404(db, project_id)
    return (
        db.query(Repository)
        .filter(Repository.project_id == project_id)
        .order_by(Repository.is_primary.desc(), Repository.created_at.desc())
        .first()
    )


@router.get("/{project_id}/github-repositories", response_model=list[RepositoryRead])
def list_project_github_repositories(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[Repository]:
    """Every repository connected to this project — multi-repo support
    (a project may have a separate frontend/backend/infra repo, etc.),
    primary first."""
    _get_project_or_404(db, project_id)
    return (
        db.query(Repository)
        .filter(Repository.project_id == project_id)
        .order_by(Repository.is_primary.desc(), Repository.created_at.desc())
        .all()
    )


# 5a. Get a project's configured Jira project, if any --------------------------------


@router.get("/{project_id}/jira-project", response_model=JiraProjectLinkRead | None)
def get_project_jira_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> JiraProjectLink | None:
    """See app/api/routes/jira_integration.py for the push-preview/push/
    sync-status actions against the Jira project this returns."""
    _get_project_or_404(db, project_id)
    return db.query(JiraProjectLink).filter(JiraProjectLink.project_id == project_id).first()


# 5b. Get a project's configured Confluence space, if any -----------------------------


@router.get("/{project_id}/confluence-space", response_model=ConfluenceSpaceLinkRead | None)
def get_project_confluence_space(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ConfluenceSpaceLink | None:
    """See app/api/routes/confluence_integration.py for the publish-preview/
    publish actions against the Confluence space this returns."""
    _get_project_or_404(db, project_id)
    return db.query(ConfluenceSpaceLink).filter(ConfluenceSpaceLink.project_id == project_id).first()


# 5c. List a project's Maintenance Agent run history -----------------------------------


@router.get("/{project_id}/maintenance-runs", response_model=list[MaintenanceRunRead])
def list_maintenance_runs(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[MaintenanceRunRead]:
    _get_project_or_404(db, project_id)
    runs = db.query(MaintenanceRun).filter(MaintenanceRun.project_id == project_id).order_by(MaintenanceRun.created_at.desc()).all()
    return [MaintenanceRunRead.from_orm_run(r) for r in runs]


# Generate Implementation Plan (rule 6) -----------------------------------------------
#
# One action: parses the approved LLD (+ story backlog) into structured,
# persisted ImplementationTask rows (see
# app/services/implementation_planner.py), saves the plan as a reviewable
# Markdown artifact, and resubmits it for review in the same step — mirrors
# app/services/revision_agent.py's "generate and auto-resubmit" pattern.
# "Approve Implementation Plan" (rule 7) is deliberately NOT a new endpoint
# — it's the existing POST /reviews/{id}/approve against the review this
# creates, the same review-decision endpoint every other stage already
# uses (which is also what makes rule 8 — coding agents cannot start
# before this is approved — work for free: `implementation.required_inputs`
# already includes `implementation_plan`, gated by the exact same
# approved-artifact mechanism as every other required input).


@router.post(
    "/{project_id}/implementation-plan/generate",
    response_model=GenerateImplementationPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_implementation_plan(
    project_id: uuid.UUID, payload: GenerateImplementationPlanRequest, db: Session = Depends(get_db)
) -> GenerateImplementationPlanResponse:
    project = _get_project_or_404(db, project_id)

    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == project_id, WorkflowNode.node_key == "implementation_planning")
        .first()
    )
    if node is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Project {project_id}'s workflow has no implementation_planning stage."
        )

    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user",
        )
    require_can_edit_stage(triggered_by, node.node_key)

    reviewer = db.get(User, payload.reviewer_id)
    if reviewer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reviewer_id {payload.reviewer_id} does not match an existing user")

    # Rule 6: cannot generate before the LLD (and story backlog) are
    # approved — the same GraphEngineService gate every stage's agent run
    # already passes through, not a bespoke check.
    graph_engine = GraphEngineService(db)
    validation = graph_engine.validate_can_run(project=project, node=node, freeform_context={})
    if not validation.can_run:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot generate — " + "; ".join(validation.reasons) + ".")

    lld_content = validation.approved_artifact_content.get("lld_document", "")
    story_backlog_content = validation.approved_artifact_content.get("story_backlog", "")

    graph_engine.mark_running(node)
    task_drafts = build_implementation_plan(lld_content=lld_content, story_backlog_content=story_backlog_content)
    plan_markdown = render_plan_markdown(task_drafts)

    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == node.id, Artifact.artifact_type == node.output_artifact_type)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None:
        artifact = Artifact(
            project_id=project.id, workflow_node=node, artifact_type=node.output_artifact_type,
            title=node.name, status=ArtifactStatus.DRAFT, created_by=triggered_by,
        )
        db.add(artifact)
        db.flush()
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="artifact.created",
            entity_type="Artifact", entity_id=artifact.id,
            extra_data={"workflow_node": node.node_key, "artifact_type": artifact.artifact_type},
        )

    last_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
    )
    next_version_number = (last_version_number or 0) + 1
    version = ArtifactVersion(
        artifact=artifact, version_number=next_version_number, content_markdown=plan_markdown,
        created_by=triggered_by,
        change_summary=f"Generated {len(task_drafts)} implementation task(s) from the approved LLD.",
    )
    db.add(version)
    db.flush()

    artifact.current_version = version
    artifact.status = ArtifactStatus.READY_FOR_REVIEW

    # Replace this node's task set wholesale (see ImplementationTask's
    # class docstring) — regenerating is a fresh plan, not a merge.
    db.query(ImplementationTask).filter(ImplementationTask.workflow_node_id == node.id).delete()
    task_rows: list[ImplementationTask] = []
    for i, draft in enumerate(task_drafts):
        row = ImplementationTask(
            project_id=project.id, workflow_node_id=node.id, artifact_id=artifact.id, artifact_version_id=version.id,
            title=draft.title, description=draft.description, linked_story=draft.linked_story,
            linked_lld_section=draft.linked_lld_section, area=ImplementationTaskArea(draft.area),
            expected_paths=draft.expected_paths, dependencies=draft.dependencies,
            acceptance_criteria=draft.acceptance_criteria, test_expectation=draft.test_expectation,
            risk_level=ImplementationTaskRiskLevel(draft.risk_level), assigned_agent_type=draft.assigned_agent_type,
            order_index=i,
        )
        db.add(row)
        task_rows.append(row)
    db.flush()

    graph_engine.mark_waiting_for_review(node)

    review = Review(artifact_version=version, workflow_node=node, reviewer_id=reviewer.id, status=ReviewStatus.PENDING)
    db.add(review)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="implementation_plan.generated",
        entity_type="Artifact", entity_id=artifact.id,
        extra_data={"task_count": len(task_rows), "version_number": next_version_number},
    )
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="artifact_version.created",
        entity_type="ArtifactVersion", entity_id=version.id,
        extra_data={"artifact_id": str(artifact.id), "version_number": next_version_number},
    )
    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="workflow_node.status_changed",
        entity_type="WorkflowNode", entity_id=node.id,
        extra_data={"node_key": node.node_key, "to": WorkflowStatus.WAITING_FOR_REVIEW.value},
    )
    record_audit_log(
        db, project_id=project.id, actor_user_id=reviewer.id, action="review.created",
        entity_type="Review", entity_id=review.id,
        extra_data={"artifact_id": str(artifact.id), "artifact_version_id": str(version.id)},
    )

    db.commit()
    db.refresh(artifact)
    db.refresh(version)
    db.refresh(review)
    for row in task_rows:
        db.refresh(row)

    return GenerateImplementationPlanResponse(
        artifact_id=artifact.id,
        artifact_version_id=version.id,
        tasks=[ImplementationTaskRead.model_validate(r) for r in task_rows],
        review=ReviewRead.from_orm_review(review),
    )


# Repo Context Preview (rule 7 of the Repo Context Builder requirements) -------------
#
# Read-only, stateless — same category as app/api/routes/github_integration.py's
# get_repository_tree/read_file endpoints (compute-and-return, no
# persistence, no audit log). Lets a human see exactly what repo context
# would be sent to a coding agent for one ImplementationTask before any
# coding agent exists to consume it.


@router.get("/{project_id}/implementation-tasks/{task_id}/repo-context-preview", response_model=RepoContextPreviewRead)
def preview_repo_context(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    snapshot_id: uuid.UUID | None = Query(default=None),
    max_files: int = Query(default=DEFAULT_MAX_FILE_COUNT, ge=1, le=500),
    max_tokens: int = Query(default=DEFAULT_MAX_CONTEXT_TOKENS, ge=100),
) -> RepoContextPreviewRead:
    _get_project_or_404(db, project_id)

    task = db.get(ImplementationTask, task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation task {task_id} not found in project {project_id}")

    repository = db.query(Repository).filter(Repository.project_id == project_id).order_by(Repository.created_at.desc()).first()
    if repository is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} has no configured GitHub repository yet.")

    if snapshot_id is not None:
        snapshot = db.get(RepositorySnapshot, snapshot_id)
        if snapshot is None or snapshot.repository_id != repository.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Repository snapshot {snapshot_id} not found for this repository.")
    else:
        snapshot = (
            db.query(RepositorySnapshot)
            .filter(RepositorySnapshot.repository_id == repository.id)
            .order_by(RepositorySnapshot.created_at.desc())
            .first()
        )
        if snapshot is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This repository has no snapshots yet — create one first.")

    # Degrades gracefully rather than failing the whole preview: a missing
    # or undecryptable credential just means every file comes back
    # metadata-only ("summary") instead of with fetched content.
    try:
        github_token = decrypt_repository_token(repository)
    except HTTPException:
        github_token = None

    service = RepoContextBuilderService(db, max_file_count=max_files, max_context_tokens=max_tokens)
    try:
        result = service.build(task=task, snapshot=snapshot, github_token=github_token)
    except GitHubIntegrationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return RepoContextPreviewRead.from_result(result)


# 6a. List one task's Testing Agent run history ---------------------------------------


@router.get("/{project_id}/implementation-tasks/{task_id}/test-runs", response_model=list[TestRunRead])
def list_task_test_runs(project_id: uuid.UUID, task_id: uuid.UUID, db: Session = Depends(get_db)) -> list[TestRunRead]:
    _get_project_or_404(db, project_id)
    task = db.get(ImplementationTask, task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation task {task_id} not found in project {project_id}")
    runs = (
        db.query(TestRun)
        .filter(TestRun.implementation_task_id == task_id)
        .order_by(TestRun.created_at.desc())
        .all()
    )
    return [TestRunRead.from_orm_run(r, review_id=get_review_id_for_test_run(db, r)) for r in runs]


# 6b. List one task's PR Review Agent run history ---------------------------------------


@router.get("/{project_id}/implementation-tasks/{task_id}/pr-review-runs", response_model=list[PRReviewRunRead])
def list_task_pr_review_runs(project_id: uuid.UUID, task_id: uuid.UUID, db: Session = Depends(get_db)) -> list[PRReviewRunRead]:
    _get_project_or_404(db, project_id)
    task = db.get(ImplementationTask, task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation task {task_id} not found in project {project_id}")
    runs = (
        db.query(PRReviewRun)
        .filter(PRReviewRun.implementation_task_id == task_id)
        .order_by(PRReviewRun.created_at.desc())
        .all()
    )
    return [PRReviewRunRead.from_orm_run(r) for r in runs]


# 6. List one task's Implementation Agent run history --------------------------------


@router.get("/{project_id}/implementation-tasks/{task_id}/implementation-runs", response_model=list[ImplementationRunRead])
def list_task_implementation_runs(project_id: uuid.UUID, task_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ImplementationRunRead]:
    """A real, confirmed bug this fixes: ImplementationRun has no
    `pull_request` relationship at all, so returning raw ORM rows here
    (as this used to) always serialized pull_request=None regardless of
    whether one actually exists — every consumer of this list (the story
    lane's Implementation/PR Review/Testing tabs, all keyed off
    `latestRun.pull_request`) would show "create a pull request first"
    forever, even immediately after a real one was created, on the very
    next refresh. GET /implementation-runs/{id} (get_implementation_run)
    already attached it correctly via ImplementationRunRead.from_orm_run
    — this route just needed the same treatment, batched here (one query
    for every run's PR link, not N) rather than one at a time."""
    _get_project_or_404(db, project_id)
    task = db.get(ImplementationTask, task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Implementation task {task_id} not found in project {project_id}")
    runs = (
        db.query(ImplementationRun)
        .filter(ImplementationRun.implementation_task_id == task_id)
        .order_by(ImplementationRun.created_at.desc())
        .all()
    )
    links_by_run_id = {
        link.implementation_run_id: link
        for link in db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id.in_([r.id for r in runs])).all()
    } if runs else {}
    return [ImplementationRunRead.from_orm_run(run, pull_request=links_by_run_id.get(run.id)) for run in runs]


# 7. Update workflow node status ---------------------------------------------------


@router.patch("/{project_id}/workflow-nodes/{node_id}", response_model=WorkflowNodeRead)
def update_workflow_node_status(
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    payload: WorkflowNodeStatusUpdate,
    db: Session = Depends(get_db),
) -> WorkflowNode:
    """Manual override — see WorkflowNodeStatusUpdate's docstring. This is
    the only place any code may set a node's status to something the graph
    engine's own rules wouldn't have produced (e.g. force-unblocking a
    rejected node, or skipping a stage that doesn't apply)."""
    _get_project_or_404(db, project_id)

    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.id == node_id, WorkflowNode.project_id == project_id)
        .first()
    )
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Workflow node {node_id} not found on project {project_id}")

    actor = db.get(User, payload.overridden_by_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"overridden_by_id {payload.overridden_by_id} does not match an existing user")
    require_can_override_node(actor)

    GraphEngineService(db).manual_override(
        node, new_status=payload.status, reason=payload.reason, actor_user_id=actor.id
    )

    db.commit()
    db.refresh(node)
    return node


# Preview a Story Crafting backlog's Jira export mapping ---------------------------
#
# Foundation only — no real Jira connection exists yet (see
# app/services/jira_export.py's module docstring and docs/architecture.md's
# MCP integrations section). This lets a human review the field mapping and
# catch validation problems before anything is ever actually pushed.


@router.post("/{project_id}/stories/preview-jira-export", response_model=JiraExportPreviewRead)
def preview_jira_export(project_id: uuid.UUID, db: Session = Depends(get_db)) -> JiraExportPreviewRead:
    _get_project_or_404(db, project_id)

    artifact = (
        db.query(Artifact)
        .filter(Artifact.project_id == project_id, Artifact.artifact_type == STORY_BACKLOG_ARTIFACT_TYPE)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Project {project_id} has no {STORY_BACKLOG_ARTIFACT_TYPE} artifact yet."
        )
    # Same gate as the plain export endpoint (see
    # app/api/routes/artifacts.py's export_story_backlog) — a backlog isn't
    # ready to preview for Jira until a human has approved it.
    if artifact.status != ArtifactStatus.APPROVED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Jira export preview requires an APPROVED story backlog (current status: {artifact.status.value}).",
        )
    if artifact.current_version is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Artifact has no version to preview.")

    stories = parse_story_backlog(artifact.current_version.content_markdown)
    preview = build_jira_export_preview(stories)

    return JiraExportPreviewRead(
        project_id=project_id,
        artifact_id=artifact.id,
        artifact_title=artifact.title,
        story_count=preview.story_count,
        valid_story_count=preview.valid_story_count,
        has_errors=preview.has_errors,
        overall_errors=preview.overall_errors,
        stories=[
            {
                "story_title": s.story_title,
                "jira_issue_type": s.jira_issue_type,
                "mapping": s.mapping,
                "validation_errors": s.validation_errors,
                "is_valid": s.is_valid,
            }
            for s in preview.stories
        ],
        push_to_jira_enabled=PUSH_TO_JIRA_ENABLED,
    )
