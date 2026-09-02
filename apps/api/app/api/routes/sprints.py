"""Sprint / SprintStory endpoints — Scrum sprint planning. See
app/models/sprint.py and app/models/sprint_story.py.

Rules enforced here (all four explicitly requested):
1. Only an approved story can be added to a sprint — see
   _require_story_is_approved below for exactly what "approved" means in
   this codebase (no dedicated Story approval status exists, so this is
   an inferred, disclosed definition).
2. A story can only get a delivery lane once ITS sprint (if it has one)
   is ACTIVE — enforced in app/api/routes/stories.py's create_story_lane,
   not here, but documented here since it's this module's rule.
3. A COMPLETED sprint can't be edited unless the actor is ADMIN — see
   _require_sprint_editable.
4. Every sprint mutation is audited (AuditLog; story-membership changes
   additionally get a StoryActivityLog row via _log_story_activity).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.stories import _get_project_or_404, _log_story_activity, _story_to_read
from app.core.database import get_db
from app.models import (
    Artifact,
    ArtifactStatus,
    ArtifactVersion,
    Review,
    ReviewStatus,
    Sprint,
    SprintStatus,
    SprintStory,
    SprintStoryStatus,
    Story,
    StoryStatus,
    User,
    UserRole,
    WorkflowNode,
)
from app.schemas.artifact import ArtifactVersionRead
from app.schemas.story import (
    AddStoryToSprintRequest,
    GeneratePlanRequest,
    RemoveStoryFromSprintRequest,
    SprintBoardItem,
    SprintBoardRead,
    SprintCreate,
    SprintLifecycleRequest,
    SprintRead,
    SprintStoryRead,
    SprintUpdate,
    StoryRead,
    UpdateSprintStoryRequest,
)
from app.services.audit import record_audit_log
from app.services.graph_engine import GraphEngineService
from app.services.permissions import require_can_edit_stage
from app.services.release_export import render_release_plan_markdown
from app.services.sprint_export import render_sprint_plan_markdown

router = APIRouter(tags=["sprints"])


def _get_sprint_or_404(db: Session, sprint_id: uuid.UUID) -> Sprint:
    sprint = db.get(Sprint, sprint_id)
    if sprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sprint {sprint_id} not found")
    return sprint


def _require_sprint_editable(sprint: Sprint, actor: User) -> None:
    """Rule 3: a COMPLETED sprint can't be edited unless the actor is
    ADMIN. CANCELLED is likewise a dead end (no reason to keep editing a
    cancelled sprint), same override."""
    if sprint.status in (SprintStatus.COMPLETED, SprintStatus.CANCELLED) and actor.role != UserRole.ADMIN:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Sprint {sprint.id} is {sprint.status.value} — only an Admin may edit it (override)."
        )


def _require_story_is_approved(db: Session, story: Story, *, target_sprint_id: uuid.UUID) -> None:
    """Rule 1: "only approved stories can be added to sprint."

    No dedicated Story approval status exists in this codebase — every
    Story row already only ever exists because its origin story_backlog
    was APPROVED (sync-from-backlog's own gate) or because a human
    created it directly. The meaningful remaining check, and the one
    enforced here, is that the story isn't already spoken for: not
    already DONE (delivered), and not already an active member of a
    *different* sprint. Flagged as an inferred default, same as
    app/services/permissions.py's own inferred-default rows — revisit if
    the product spec ever states "approved" more precisely.
    """
    if story.status == StoryStatus.DONE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Story {story.id} is already DONE — it cannot be added to a sprint.")

    existing = (
        db.query(SprintStory)
        .filter(SprintStory.story_id == story.id, SprintStory.status != SprintStoryStatus.REMOVED)
        .first()
    )
    if existing is not None and existing.sprint_id != target_sprint_id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Story {story.id} is already planned into another sprint.")


@router.post("/sprints", response_model=SprintRead, status_code=status.HTTP_201_CREATED)
def create_sprint(payload: SprintCreate, db: Session = Depends(get_db)) -> SprintRead:
    project = _get_project_or_404(db, payload.project_id)
    creator = db.get(User, payload.created_by_id)
    if creator is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"created_by_id {payload.created_by_id} does not match an existing user")
    require_can_edit_stage(creator, "sprint_planning")

    sprint = Sprint(
        project_id=project.id, name=payload.name, goal=payload.goal, start_date=payload.start_date,
        end_date=payload.end_date, capacity_points=payload.capacity_points, created_by_id=creator.id,
    )
    db.add(sprint)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=creator.id, action="sprint.created",
        entity_type="Sprint", entity_id=sprint.id, extra_data={"name": sprint.name},
    )

    db.commit()
    db.refresh(sprint)
    return SprintRead.model_validate(sprint)


@router.get("/projects/{project_id}/sprints", response_model=list[SprintRead])
def list_sprints(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[SprintRead]:
    _get_project_or_404(db, project_id)
    sprints = db.query(Sprint).filter(Sprint.project_id == project_id).order_by(Sprint.created_at.desc()).all()
    return [SprintRead.model_validate(s) for s in sprints]


@router.patch("/sprints/{sprint_id}", response_model=SprintRead)
def update_sprint(sprint_id: uuid.UUID, payload: SprintUpdate, db: Session = Depends(get_db)) -> SprintRead:
    sprint = _get_sprint_or_404(db, sprint_id)
    actor = db.get(User, payload.updated_by_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"updated_by_id {payload.updated_by_id} does not match an existing user")
    require_can_edit_stage(actor, "sprint_planning")
    _require_sprint_editable(sprint, actor)

    changed_fields: list[str] = []
    for field_name in ("name", "goal", "start_date", "end_date", "capacity_points"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(sprint, field_name, value)
            changed_fields.append(field_name)

    db.flush()
    if changed_fields:
        record_audit_log(
            db, project_id=sprint.project_id, actor_user_id=actor.id, action="sprint.updated",
            entity_type="Sprint", entity_id=sprint.id, extra_data={"fields": changed_fields},
        )

    db.commit()
    db.refresh(sprint)
    return SprintRead.model_validate(sprint)


@router.post("/sprints/{sprint_id}/stories", response_model=SprintStoryRead, status_code=status.HTTP_201_CREATED)
def add_story_to_sprint(sprint_id: uuid.UUID, payload: AddStoryToSprintRequest, db: Session = Depends(get_db)) -> SprintStory:
    sprint = _get_sprint_or_404(db, sprint_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "sprint_planning")
    _require_sprint_editable(sprint, actor)

    story = db.get(Story, payload.story_id)
    if story is None or story.project_id != sprint.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"story_id {payload.story_id} does not match a story in this project")
    _require_story_is_approved(db, story, target_sprint_id=sprint.id)

    if payload.assigned_owner_id is not None and db.get(User, payload.assigned_owner_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"assigned_owner_id {payload.assigned_owner_id} does not match an existing user")

    existing = db.query(SprintStory).filter(SprintStory.sprint_id == sprint.id, SprintStory.story_id == story.id).first()
    if existing is not None and existing.status != SprintStoryStatus.REMOVED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Story {story.id} is already in sprint {sprint.id}.")

    if existing is not None:
        # Re-adding a previously-removed story reuses its row rather than
        # violating the (sprint_id, story_id) unique constraint.
        existing.status = SprintStoryStatus.PLANNED
        existing.planned_points = payload.planned_points
        existing.assigned_owner_id = payload.assigned_owner_id
        sprint_story = existing
    else:
        sprint_story = SprintStory(
            sprint_id=sprint.id, story_id=story.id, planned_points=payload.planned_points,
            assigned_owner_id=payload.assigned_owner_id, status=SprintStoryStatus.PLANNED,
        )
        db.add(sprint_story)
    db.flush()

    story.sprint_id = sprint.id
    story.status = StoryStatus.IN_SPRINT

    record_audit_log(
        db, project_id=sprint.project_id, actor_user_id=actor.id, action="sprint.story_added",
        entity_type="SprintStory", entity_id=sprint_story.id,
        extra_data={"sprint_id": str(sprint.id), "story_id": str(story.id), "planned_points": payload.planned_points},
    )
    _log_story_activity(
        db, story=story, action="sprint.story_added", actor=actor,
        details={"sprint_id": str(sprint.id), "planned_points": payload.planned_points},
    )

    db.commit()
    db.refresh(sprint_story)
    return sprint_story


@router.patch("/sprints/{sprint_id}/stories/{story_id}", response_model=SprintStoryRead)
def update_sprint_story(
    sprint_id: uuid.UUID, story_id: uuid.UUID, payload: UpdateSprintStoryRequest, db: Session = Depends(get_db)
) -> SprintStory:
    sprint = _get_sprint_or_404(db, sprint_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "sprint_planning")
    _require_sprint_editable(sprint, actor)

    sprint_story = (
        db.query(SprintStory)
        .filter(SprintStory.sprint_id == sprint_id, SprintStory.story_id == story_id, SprintStory.status != SprintStoryStatus.REMOVED)
        .first()
    )
    if sprint_story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} is not currently in sprint {sprint_id}.")

    if payload.assigned_owner_id is not None:
        if db.get(User, payload.assigned_owner_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"assigned_owner_id {payload.assigned_owner_id} does not match an existing user")
        sprint_story.assigned_owner_id = payload.assigned_owner_id
    if payload.planned_points is not None:
        sprint_story.planned_points = payload.planned_points
    db.flush()

    record_audit_log(
        db, project_id=sprint.project_id, actor_user_id=actor.id, action="sprint.story_updated",
        entity_type="SprintStory", entity_id=sprint_story.id,
        extra_data={"planned_points": payload.planned_points, "assigned_owner_id": str(payload.assigned_owner_id) if payload.assigned_owner_id else None},
    )

    db.commit()
    db.refresh(sprint_story)
    return sprint_story


@router.delete("/sprints/{sprint_id}/stories/{story_id}", response_model=SprintStoryRead)
def remove_story_from_sprint(
    sprint_id: uuid.UUID, story_id: uuid.UUID, payload: RemoveStoryFromSprintRequest, db: Session = Depends(get_db)
) -> SprintStory:
    """Soft-delete — sets the SprintStory row's status to REMOVED rather
    than deleting it, so the sprint's full membership history (including
    what was pulled back out, and when) is never lost."""
    sprint = _get_sprint_or_404(db, sprint_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "sprint_planning")
    _require_sprint_editable(sprint, actor)

    sprint_story = (
        db.query(SprintStory)
        .filter(SprintStory.sprint_id == sprint_id, SprintStory.story_id == story_id, SprintStory.status != SprintStoryStatus.REMOVED)
        .first()
    )
    if sprint_story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} is not currently in sprint {sprint_id}.")

    sprint_story.status = SprintStoryStatus.REMOVED
    db.flush()

    story = db.get(Story, story_id)
    if story is not None and story.sprint_id == sprint_id:
        story.sprint_id = None
        story.status = StoryStatus.PENDING

    record_audit_log(
        db, project_id=sprint.project_id, actor_user_id=actor.id, action="sprint.story_removed",
        entity_type="SprintStory", entity_id=sprint_story.id,
        extra_data={"sprint_id": str(sprint.id), "story_id": str(story_id)},
    )
    if story is not None:
        _log_story_activity(db, story=story, action="sprint.story_removed", actor=actor, details={"sprint_id": str(sprint.id)})

    db.commit()
    db.refresh(sprint_story)
    return sprint_story


@router.post("/sprints/{sprint_id}/start", response_model=SprintRead)
def start_sprint(sprint_id: uuid.UUID, payload: SprintLifecycleRequest, db: Session = Depends(get_db)) -> SprintRead:
    sprint = _get_sprint_or_404(db, sprint_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "sprint_planning")

    if sprint.status != SprintStatus.PLANNED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Sprint {sprint_id} is {sprint.status.value}, not PLANNED — cannot start it.")

    sprint.status = SprintStatus.ACTIVE
    db.flush()

    record_audit_log(
        db, project_id=sprint.project_id, actor_user_id=actor.id, action="sprint.started",
        entity_type="Sprint", entity_id=sprint.id, extra_data={},
    )

    db.commit()
    db.refresh(sprint)
    return SprintRead.model_validate(sprint)


@router.post("/sprints/{sprint_id}/complete", response_model=SprintRead)
def complete_sprint(sprint_id: uuid.UUID, payload: SprintLifecycleRequest, db: Session = Depends(get_db)) -> SprintRead:
    sprint = _get_sprint_or_404(db, sprint_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "sprint_planning")

    if sprint.status != SprintStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Sprint {sprint_id} is {sprint.status.value}, not ACTIVE — cannot complete it.")

    sprint.status = SprintStatus.COMPLETED
    db.flush()

    record_audit_log(
        db, project_id=sprint.project_id, actor_user_id=actor.id, action="sprint.completed",
        entity_type="Sprint", entity_id=sprint.id, extra_data={},
    )

    db.commit()
    db.refresh(sprint)
    return SprintRead.model_validate(sprint)


@router.get("/sprints/{sprint_id}/board", response_model=SprintBoardRead)
def get_sprint_board(sprint_id: uuid.UUID, db: Session = Depends(get_db)) -> SprintBoardRead:
    sprint = _get_sprint_or_404(db, sprint_id)
    sprint_stories = (
        db.query(SprintStory)
        .filter(SprintStory.sprint_id == sprint_id, SprintStory.status != SprintStoryStatus.REMOVED)
        .order_by(SprintStory.created_at.asc())
        .all()
    )

    items: list[SprintBoardItem] = []
    planned_points_total = 0
    for sprint_story in sprint_stories:
        story = db.get(Story, sprint_story.story_id)
        if story is None:
            continue
        planned_points_total += sprint_story.planned_points or 0
        items.append(SprintBoardItem(sprint_story=SprintStoryRead.model_validate(sprint_story), story=_story_to_read(db, story)))

    return SprintBoardRead(
        sprint=SprintRead.model_validate(sprint),
        items=items,
        planned_points_total=planned_points_total,
        capacity_points=sprint.capacity_points,
        over_capacity=sprint.capacity_points is not None and planned_points_total > sprint.capacity_points,
    )


# --- Deterministic Markdown renders (Sprint Planning / Release Planning stages) ---


def _save_new_version(db: Session, *, node: WorkflowNode, artifact_type: str, markdown: str, created_by: User, change_summary: str) -> ArtifactVersion:
    """Create-or-reuse-Artifact-then-new-Version — the same pattern every
    other stage's generation route already uses (see e.g.
    generate_implementation_plan in app/api/routes/projects.py)."""
    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == node.id, Artifact.artifact_type == artifact_type)
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if artifact is None:
        artifact = Artifact(
            project_id=node.project_id, workflow_node=node, artifact_type=artifact_type,
            title=node.name, status=ArtifactStatus.DRAFT, created_by=created_by,
        )
        db.add(artifact)
        db.flush()
        record_audit_log(
            db, project_id=node.project_id, actor_user_id=created_by.id, action="artifact.created",
            entity_type="Artifact", entity_id=artifact.id,
            extra_data={"workflow_node": node.node_key, "artifact_type": artifact_type},
        )

    next_version_number = (
        db.query(ArtifactVersion.version_number)
        .filter(ArtifactVersion.artifact_id == artifact.id)
        .order_by(ArtifactVersion.version_number.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1
    version = ArtifactVersion(
        artifact=artifact, version_number=next_version_number, content_markdown=markdown,
        created_by=created_by, change_summary=change_summary,
    )
    db.add(version)
    db.flush()
    artifact.current_version = version
    db.flush()
    return version


@router.post("/sprints/{sprint_id}/generate-plan", response_model=ArtifactVersionRead, status_code=status.HTTP_201_CREATED)
def generate_sprint_plan(sprint_id: uuid.UUID, payload: GeneratePlanRequest, db: Session = Depends(get_db)) -> ArtifactVersionRead:
    sprint = _get_sprint_or_404(db, sprint_id)
    project = _get_project_or_404(db, sprint.project_id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "sprint_planning")

    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "sprint_planning")
        .first()
    )
    if node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no sprint_planning stage.")

    stories = db.query(Story).filter(Story.sprint_id == sprint.id).order_by(Story.created_at.asc()).all()
    markdown = render_sprint_plan_markdown(sprint, stories)
    version = _save_new_version(
        db, node=node, artifact_type=node.output_artifact_type, markdown=markdown, created_by=triggered_by,
        change_summary=f"Rendered from {len(stories)} sprint stor{'y' if len(stories) == 1 else 'ies'}.",
    )

    # No human approval gate on this stage (a lightweight grouping stage,
    # not a drafted document) — complete it and unlock Release Planning
    # directly, the same two calls a Review approval would otherwise make.
    graph_engine = GraphEngineService(db)
    graph_engine.mark_completed(node)
    graph_engine.unlock_next_nodes(node)

    db.commit()
    db.refresh(version)
    return ArtifactVersionRead.from_orm_version(version)


@router.post("/sprints/{sprint_id}/generate-release-plan", response_model=ArtifactVersionRead, status_code=status.HTTP_201_CREATED)
def generate_release_plan(sprint_id: uuid.UUID, payload: GeneratePlanRequest, db: Session = Depends(get_db)) -> ArtifactVersionRead:
    sprint = _get_sprint_or_404(db, sprint_id)
    project = _get_project_or_404(db, sprint.project_id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "release_planning")
    reviewer = db.get(User, payload.reviewer_id) if payload.reviewer_id else None
    if reviewer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reviewer_id is required and must match an existing user")

    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "release_planning")
        .first()
    )
    if node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no release_planning stage.")

    graph_engine = GraphEngineService(db)
    validation = graph_engine.validate_can_run(project=project, node=node, freeform_context={})
    if not validation.can_run:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot generate — " + "; ".join(validation.reasons) + ".")

    stories = db.query(Story).filter(Story.sprint_id == sprint.id).order_by(Story.created_at.asc()).all()
    markdown = render_release_plan_markdown(db, sprint, stories)
    version = _save_new_version(
        db, node=node, artifact_type=node.output_artifact_type, markdown=markdown, created_by=triggered_by,
        change_summary=f"Rendered release readiness for {len(stories)} sprint stor{'y' if len(stories) == 1 else 'ies'}.",
    )

    # Release Planning DOES require human approval (requiresHumanApproval:
    # true in the template) — same waiting-for-review + Review pattern
    # every other approval-gated stage uses.
    graph_engine.mark_waiting_for_review(node)
    review = Review(artifact_version=version, workflow_node=node, reviewer_id=reviewer.id, status=ReviewStatus.PENDING)
    db.add(review)
    db.flush()

    db.commit()
    db.refresh(version)
    return ArtifactVersionRead.from_orm_version(version)
