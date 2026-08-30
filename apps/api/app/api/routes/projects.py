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
    Project,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
    User,
    WorkflowEdge,
    WorkflowNode,
)
from app.schemas.agent_run import AgentRunRead
from app.schemas.artifact import ArtifactRead
from app.schemas.jira_export import JiraExportPreviewRead
from app.schemas.project import ProjectCreate, ProjectListResponse, ProjectRead, ProjectUpdate
from app.schemas.workflow import WorkflowEdgeRead, WorkflowNodeRead, WorkflowNodeStatusUpdate
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.jira_export import PUSH_TO_JIRA_ENABLED, build_jira_export_preview
from app.services.permissions import require_can_override_node, require_can_update_project
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

    try:
        template = load_workflow_template(payload.workflow_template_file)
    except WorkflowTemplateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    project = Project(
        name=payload.name,
        description=payload.description,
        business_owner=payload.business_owner,
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
def list_project_workflow_nodes(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[WorkflowNode]:
    _get_project_or_404(db, project_id)
    return (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == project_id)
        .order_by(WorkflowNode.order_index)
        .all()
    )


# Get project workflow edges ---------------------------------------------------


@router.get("/{project_id}/workflow-edges", response_model=list[WorkflowEdgeRead])
def list_project_workflow_edges(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[WorkflowEdge]:
    """Needed alongside workflow-nodes to render the graph — an edge list
    with no nodes (or vice versa) can't be drawn."""
    _get_project_or_404(db, project_id)
    return db.query(WorkflowEdge).filter(WorkflowEdge.project_id == project_id).all()


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
