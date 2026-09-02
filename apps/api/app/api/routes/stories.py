"""Scrum story endpoints: persist stories out of an approved Story
Crafting backlog (or directly), let a human review/edit/assign them, and
create each story's own parallel delivery lane. See docs/architecture.md
and workflows/scrum-story-lanes-*.json. Sprint/SprintStory routes live in
app/api/routes/sprints.py.

Jira sync has no new endpoint here — app/services/jira_push_preview.py's
existing per-story push flow already keys off a story's title, the exact
same title `Story.title` is populated from (see Story's class docstring),
so it keeps working against this table unmodified.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    Artifact,
    ArtifactStatus,
    ImplementationTask,
    ImplementationTaskArea,
    JiraIssueLink,
    JiraSourceType,
    Project,
    Sprint,
    SprintStatus,
    Story,
    StoryActivityLog,
    StoryArtifact,
    StoryAssignee,
    StoryDeliveryLane,
    StoryDeliveryLaneStatus,
    StoryDeliveryNode,
    StoryDeliveryNodeStatus,
    StoryStatus,
    User,
    UserRole,
    WorkflowNode,
)
from app.schemas.implementation_task import ImplementationTaskRead
from app.schemas.story import (
    AssignStoryOwnerRequest,
    CreateStoryLaneRequest,
    DraftStoryImplementationPlanRequest,
    DraftStoryImplementationPlanResponse,
    DraftStoryLldRequest,
    DraftStoryLldResponse,
    DraftStoryTestScenariosRequest,
    DraftStoryTestScenariosResponse,
    StoryArtifactRead,
    StoryCreate,
    StoryDeliveryLaneRead,
    StoryDeliveryNodeRead,
    StoryListResponse,
    StoryRead,
    StoryUpdate,
    SyncStoriesFromBacklogRequest,
    SyncStoriesResponse,
    UpdateLaneNodeStatusRequest,
)
from app.services.audit import record_audit_log
from app.services.implementation_planner import AREA_TO_AGENT_TYPE, _infer_area
from app.services.markdown_sections import find_section
from app.services.permissions import require_can_edit_stage
from app.services.story_delivery import StoryDeliveryError, advance_lane, create_story_delivery_lane
from app.services.story_done_gate import evaluate_story_done_gate
from app.services.story_jira_sync import try_post_story_done_comment
from app.services.story_export import parse_story_backlog
from app.services.story_implementation_plan_agent import (
    STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE,
    StoryImplementationPlanError,
    run_story_implementation_plan_agent,
)
from app.services.story_test_scenarios_agent import (
    STORY_TEST_SCENARIOS_ARTIFACT_TYPE,
    StoryTestScenariosError,
    run_story_test_scenarios_agent,
)
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE, StoryLldError, run_story_lld_agent
from app.services.testing_agent import STORY_TEST_REPORT_ARTIFACT_TYPE

router = APIRouter(tags=["stories"])

# A suggestion only — a human can assign any owner regardless (see
# POST /stories/{id}/assign). Only used as a fallback when the
# story-crafting-agent didn't state its own Suggested Owner Role.
_AREA_TO_SUGGESTED_ROLE: dict[str, str] = {
    "BACKEND": "DEVELOPER",
    "FRONTEND": "DEVELOPER",
    "DATABASE": "DEVELOPER",
    "TESTING": "QA",
    "INFRA": "DEVOPS",
    "DOCS": "BA",
}


def _suggest_owner_role(*, feature: str, title: str, user_story: str) -> str:
    area = _infer_area(f"{feature} {title} {user_story}")
    return _AREA_TO_SUGGESTED_ROLE.get(area, "DEVELOPER")


def _parse_story_points(raw: str) -> int | None:
    """The agent's "Story Points Estimate" is free text (a plain number,
    or a sizing label like "M" / "5 (Fibonacci)") — only seed Story.
    story_points when it parses cleanly as a plain integer; anything else
    is left for a human to fill in via PATCH /stories/{id} rather than
    guessed at."""
    stripped = raw.strip()
    return int(stripped) if stripped.isdigit() else None


def _lane_status_label(story: Story) -> str:
    """The story's own StoryDeliveryLane (see app/services/story_delivery.py)
    — its current node's name/status, or "No delivery lane yet" if none
    exists. Unlike an earlier lane implementation in this codebase's
    history, a story's lane is never a subset of the project-level
    WorkflowNode graph — it's this dedicated table, so this reads
    `story.delivery_lane` directly rather than querying WorkflowNode."""
    lane = story.delivery_lane
    if lane is None:
        return "No delivery lane yet"
    if lane.current_node is not None:
        return f"{lane.current_node.name} ({lane.current_node.status.value})"
    return lane.status.value


def _log_story_activity(
    db: Session, *, story: Story, action: str, actor: User | None,
    lane_id: uuid.UUID | None = None, node_id: uuid.UUID | None = None, details: dict | None = None,
) -> None:
    """Writes a StoryActivityLog row — additive alongside the generic
    AuditLog (see record_audit_log calls throughout this file), not a
    replacement: existing AuditLog-based consumers keep working
    unchanged, and this module's own activity feed reads from here."""
    db.add(
        StoryActivityLog(
            story_id=story.id, lane_id=lane_id, node_id=node_id, action=action,
            actor_user_id=actor.id if actor is not None else None, details=details,
        )
    )


def _story_to_read(db: Session, story: Story) -> StoryRead:
    """StoryRead plus two fields computed off other tables, not stored on
    Story itself: lane_status (see _lane_status_label above) and
    jira_status (the latest JiraIssueLink pushed for this story's title,
    if any — see app/services/jira_push_preview.py for how that push
    happens). `jira_issue_key`/`jira_issue_url` prefer the live
    JiraIssueLink when one exists, falling back to Story's own stored
    `jira_issue_key` (settable directly via PATCH /stories/{id} for a
    manual correction) otherwise."""
    base = StoryRead.model_validate(story)

    jira_link = (
        db.query(JiraIssueLink)
        .filter(
            JiraIssueLink.project_id == story.project_id,
            JiraIssueLink.source_type == JiraSourceType.STORY,
            JiraIssueLink.source_key == story.title,
        )
        .order_by(JiraIssueLink.created_at.desc())
        .first()
    )
    jira_status = (jira_link.jira_status or "Synced") if jira_link is not None else ("Synced" if story.jira_issue_key else "Not Synced")

    return base.model_copy(
        update={
            "lane_status": _lane_status_label(story),
            "jira_status": jira_status,
            "jira_issue_key": (jira_link.jira_issue_key if jira_link is not None else story.jira_issue_key),
            "jira_issue_url": jira_link.jira_issue_url if jira_link is not None else story.jira_issue_url,
        }
    )


def _get_project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


def _get_story_or_404(db: Session, story_id: uuid.UUID) -> Story:
    story = db.get(Story, story_id)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} not found")
    return story


def _get_approved_story_crafting_artifact(db: Session, project: Project) -> Artifact:
    node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "story_crafting")
        .first()
    )
    if node is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Project {project.id}'s workflow has no story_crafting stage.")
    artifact = (
        db.query(Artifact)
        .filter(Artifact.workflow_node_id == node.id, Artifact.status == ArtifactStatus.APPROVED)
        .order_by(Artifact.updated_at.desc())
        .first()
    )
    if artifact is None or artifact.current_version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Story Crafting has no approved backlog yet.")
    return artifact


@router.post(
    "/projects/{project_id}/stories/sync-from-backlog",
    response_model=SyncStoriesResponse,
    status_code=status.HTTP_201_CREATED,
)
def sync_stories_from_backlog(
    project_id: uuid.UUID, payload: SyncStoriesFromBacklogRequest, db: Session = Depends(get_db)
) -> SyncStoriesResponse:
    project = _get_project_or_404(db, project_id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "story_crafting")

    artifact = _get_approved_story_crafting_artifact(db, project)
    parsed = parse_story_backlog(artifact.current_version.content_markdown)

    existing_titles = {
        s.title for s in db.query(Story.title).filter(Story.project_id == project.id).all()
    }

    created: list[Story] = []
    already_existed = 0
    for parsed_story in parsed:
        if parsed_story.title in existing_titles:
            already_existed += 1
            continue
        row = Story(
            project_id=project.id,
            source_artifact_version_id=artifact.current_version_id,
            story_type=payload.story_type,
            status=StoryStatus.PENDING,
            epic=parsed_story.epic,
            feature=parsed_story.feature,
            title=parsed_story.title,
            user_story=parsed_story.user_story,
            priority=parsed_story.priority,
            dependencies=parsed_story.dependencies,
            acceptance_criteria=parsed_story.acceptance_criteria,
            definition_of_done=parsed_story.definition_of_done,
            # Prefer the agent's own "Suggested Owner Role" field; only
            # fall back to the keyword heuristic when the agent didn't
            # state one (an older-format backlog, or a malformed story).
            suggested_owner_role=parsed_story.suggested_owner_role.strip().upper()
            or _suggest_owner_role(feature=parsed_story.feature, title=parsed_story.title, user_story=parsed_story.user_story),
            story_points=_parse_story_points(parsed_story.story_points_estimate),
            business_value=parsed_story.business_value,
            technical_areas=parsed_story.technical_areas,
            jira_issue_type=parsed_story.jira_issue_type,
            suggested_subtasks=parsed_story.suggested_subtasks,
            release_readiness_criteria=parsed_story.release_readiness_criteria,
            created_by_id=triggered_by.id,
        )
        db.add(row)
        db.flush()
        created.append(row)
        _log_story_activity(db, story=row, action="story.created", actor=triggered_by, details={"source": "sync_from_backlog"})
        record_audit_log(
            db, project_id=project.id, actor_user_id=triggered_by.id, action="story.created",
            entity_type="Story", entity_id=row.id,
            extra_data={"title": row.title, "story_type": row.story_type.value},
        )

    db.commit()
    for row in created:
        db.refresh(row)
    return SyncStoriesResponse(created=[_story_to_read(db, r) for r in created], already_existed=already_existed)


@router.post("/stories", response_model=StoryRead, status_code=status.HTTP_201_CREATED)
def create_story(payload: StoryCreate, db: Session = Depends(get_db)) -> StoryRead:
    """Direct story creation — no story_backlog document required. See
    StoryCreate's docstring for how this differs from sync-from-backlog."""
    project = _get_project_or_404(db, payload.project_id)
    creator = db.get(User, payload.created_by_id)
    if creator is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"created_by_id {payload.created_by_id} does not match an existing user")
    require_can_edit_stage(creator, "story_crafting")

    existing = db.query(Story).filter(Story.project_id == project.id, Story.title == payload.title).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A story titled '{payload.title}' already exists in this project.")

    if payload.sprint_id is not None and db.get(Sprint, payload.sprint_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"sprint_id {payload.sprint_id} does not match an existing sprint")

    story = Story(
        project_id=project.id,
        title=payload.title,
        description=payload.description,
        story_type=payload.mode,
        status=StoryStatus.IN_SPRINT if payload.sprint_id is not None else StoryStatus.PENDING,
        user_story=payload.user_story,
        business_value=payload.business_value,
        acceptance_criteria=payload.acceptance_criteria,
        priority=payload.priority,
        story_points=payload.story_points,
        suggested_owner_role=payload.suggested_owner_role,
        sprint_id=payload.sprint_id,
        created_by_id=creator.id,
    )
    db.add(story)
    db.flush()

    _log_story_activity(db, story=story, action="story.created", actor=creator, details={"source": "direct"})
    record_audit_log(
        db, project_id=project.id, actor_user_id=creator.id, action="story.created",
        entity_type="Story", entity_id=story.id, extra_data={"title": story.title, "story_type": story.story_type.value, "source": "direct"},
    )

    db.commit()
    db.refresh(story)
    return _story_to_read(db, story)


@router.get("/projects/{project_id}/stories", response_model=StoryListResponse)
def list_stories(project_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryListResponse:
    _get_project_or_404(db, project_id)
    stories = db.query(Story).filter(Story.project_id == project_id).order_by(Story.created_at.asc()).all()
    return StoryListResponse(items=[_story_to_read(db, s) for s in stories], total=len(stories))


@router.patch("/stories/{story_id}", response_model=StoryRead)
def update_story(story_id: uuid.UUID, payload: StoryUpdate, db: Session = Depends(get_db)) -> StoryRead:
    """Edits a story's own copied fields. Owner assignment is deliberately
    NOT handled here — see POST /stories/{id}/assign, which also writes
    the StoryAssignee history row this endpoint would otherwise skip."""
    story = _get_story_or_404(db, story_id)
    actor = db.get(User, payload.updated_by_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"updated_by_id {payload.updated_by_id} does not match an existing user")
    require_can_edit_stage(actor, "story_crafting")

    changed_fields: list[str] = []
    for field_name in (
        "title", "description", "user_story", "priority", "dependencies", "acceptance_criteria", "definition_of_done",
        "suggested_owner_role", "story_points", "business_value", "technical_areas", "jira_issue_type",
        "jira_issue_key", "suggested_subtasks", "release_readiness_criteria",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(story, field_name, value)
            changed_fields.append(field_name)

    db.flush()
    if changed_fields:
        _log_story_activity(db, story=story, action="story.updated", actor=actor, details={"fields": changed_fields})
        record_audit_log(
            db, project_id=story.project_id, actor_user_id=actor.id, action="story.updated",
            entity_type="Story", entity_id=story.id, extra_data={"fields": changed_fields},
        )

    db.commit()
    db.refresh(story)
    return _story_to_read(db, story)


@router.post("/stories/{story_id}/assign", response_model=StoryRead)
def assign_story_owner(story_id: uuid.UUID, payload: AssignStoryOwnerRequest, db: Session = Depends(get_db)) -> StoryRead:
    """Assigns (or re-assigns) a story's owner — writes both the
    denormalized `Story.owner_user_id` (for quick reads) and a new
    StoryAssignee row (closing out the previous one, if any) so the full
    assignment history is preserved. See StoryAssignee's class docstring."""
    story = _get_story_or_404(db, story_id)
    actor = db.get(User, payload.assigned_by_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"assigned_by_id {payload.assigned_by_id} does not match an existing user")
    require_can_edit_stage(actor, "story_crafting")

    owner = db.get(User, payload.owner_user_id)
    if owner is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"owner_user_id {payload.owner_user_id} does not match an existing user")

    now = datetime.now(timezone.utc)
    current = db.query(StoryAssignee).filter(StoryAssignee.story_id == story.id, StoryAssignee.unassigned_at.is_(None)).first()
    if current is not None:
        current.unassigned_at = now

    db.add(StoryAssignee(story_id=story.id, user_id=owner.id, assigned_by_id=actor.id, assigned_at=now))
    story.owner_user_id = owner.id
    db.flush()

    _log_story_activity(db, story=story, action="story.assigned", actor=actor, details={"owner_user_id": str(owner.id)})
    record_audit_log(
        db, project_id=story.project_id, actor_user_id=actor.id, action="story.assigned",
        entity_type="Story", entity_id=story.id, extra_data={"owner_user_id": str(owner.id)},
    )

    db.commit()
    db.refresh(story)
    return _story_to_read(db, story)


def _ensure_story_implementation_task(db: Session, *, story: Story, created_by: User) -> ImplementationTask:
    """Story-level implementation workflow — this story's one
    ImplementationTask (see app/models/implementation_task.py: rule "one
    run is always exactly one story" holds structurally because this is
    the only task ever created for this story, and ImplementationTask.story_id
    is a single nullable FK, never a collection). Idempotent: called every
    time IMPLEMENTATION unlocks, but only ever creates the row once.

    `workflow_node_id`/`artifact_id`/`artifact_version_id` are left null —
    there is no project-level WorkflowNode/Artifact/ArtifactVersion for a
    StoryDeliveryNode to link to; every consumer of those three either
    null-checks (app/services/repo_context_builder.py) or doesn't touch
    them for a story-scoped task."""
    existing = db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).first()
    if existing is not None:
        return existing

    area = ImplementationTaskArea(_infer_area(f"{story.feature} {story.title} {story.user_story}"))
    task = ImplementationTask(
        project_id=story.project_id,
        story_id=story.id,
        title=story.title,
        description=story.user_story or story.description,
        linked_story=story.title,
        area=area,
        acceptance_criteria=story.acceptance_criteria,
        assigned_agent_type=AREA_TO_AGENT_TYPE.get(area.value, AREA_TO_AGENT_TYPE["BACKEND"]),
        order_index=0,
    )
    db.add(task)
    db.flush()
    _log_story_activity(db, story=story, action="implementation_task.created", actor=created_by, details={"task_id": str(task.id)})
    return task


@router.post("/stories/{story_id}/lane", response_model=StoryRead, status_code=status.HTTP_201_CREATED)
def create_story_lane(story_id: uuid.UUID, payload: CreateStoryLaneRequest, db: Session = Depends(get_db)) -> StoryRead:
    """Materializes the story's own 10-node StoryDeliveryLane (see
    app/services/story_delivery.py) — a dedicated, self-contained graph
    for this story, not a subset of the project-level WorkflowNode graph."""
    story = _get_story_or_404(db, story_id)
    project = _get_project_or_404(db, story.project_id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "story_crafting")

    if story.lane_created_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Story {story_id} already has a delivery lane.")

    # Story lanes cannot be created before Story Crafting approval.
    _get_approved_story_crafting_artifact(db, project)

    # Sprint planning rule: a story that's planned into a sprint can only
    # get a delivery lane once that sprint is ACTIVE — a PLANNED sprint's
    # stories aren't being worked yet, so their lanes shouldn't exist yet
    # either. A story with no sprint at all is unaffected (unchanged
    # behavior — sprints are optional).
    if story.sprint_id is not None:
        sprint = db.get(Sprint, story.sprint_id)
        if sprint is not None and sprint.status != SprintStatus.ACTIVE:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Story {story_id}'s sprint is {sprint.status.value}, not ACTIVE — cannot create a delivery lane yet.",
            )

    try:
        create_story_delivery_lane(db, story=story, created_by=triggered_by)
    except StoryDeliveryError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    story.status = StoryStatus.LANE_ACTIVE
    story.lane_created_at = datetime.now(timezone.utc)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=triggered_by.id, action="story_lane.created",
        entity_type="Story", entity_id=story.id, extra_data={"title": story.title},
    )

    db.commit()
    db.refresh(story)
    return _story_to_read(db, story)


@router.get("/stories/{story_id}/lane", response_model=StoryDeliveryLaneRead)
def get_story_delivery_lane(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryDeliveryLane:
    story = _get_story_or_404(db, story_id)
    if story.delivery_lane is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} has no delivery lane yet.")
    return story.delivery_lane


@router.get("/delivery-lanes/{lane_id}/nodes", response_model=list[StoryDeliveryNodeRead])
def list_lane_nodes(lane_id: uuid.UUID, db: Session = Depends(get_db)) -> list[StoryDeliveryNode]:
    lane = db.get(StoryDeliveryLane, lane_id)
    if lane is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Delivery lane {lane_id} not found")
    return (
        db.query(StoryDeliveryNode)
        .filter(StoryDeliveryNode.lane_id == lane_id)
        .order_by(StoryDeliveryNode.order_index)
        .all()
    )


_QA_APPROVAL_REQUIRED_EVIDENCE_SECTION = "Test Evidence"


def _require_test_evidence(test_report: StoryArtifact | None) -> str | None:
    """Requirement 6 — structural check only (the heading exists and has
    real content), same limitation app/services/graph_engine.py's
    validate_evidence_requirement discloses for the project-level
    equivalent: it does not verify the substance of what's written there."""
    if test_report is None:
        return "Cannot grant QA Approval — no test report has been generated for this story yet."
    section = find_section(test_report.content_markdown, _QA_APPROVAL_REQUIRED_EVIDENCE_SECTION)
    if section is None:
        return f"Cannot grant QA Approval — the test report has no '{_QA_APPROVAL_REQUIRED_EVIDENCE_SECTION}' section."
    if not section["content"].strip():
        return f"Cannot grant QA Approval — the '{_QA_APPROVAL_REQUIRED_EVIDENCE_SECTION}' section is present but empty."
    return None


@router.patch("/delivery-lane-nodes/{node_id}", response_model=StoryDeliveryNodeRead)
def update_lane_node_status(
    node_id: uuid.UUID, payload: UpdateLaneNodeStatusRequest, db: Session = Depends(get_db)
) -> StoryDeliveryNode:
    """Moves one lane node to a new status. Completing a node
    (`status=COMPLETED`) automatically unlocks its immediate successor —
    see app/services/story_delivery.py's advance_lane, since the default
    lane is a straight sequence with no branching to resolve."""
    node = db.get(StoryDeliveryNode, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Delivery lane node {node_id} not found")
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")

    try:
        new_status = StoryDeliveryNodeStatus(payload.status)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in StoryDeliveryNodeStatus)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid status '{payload.status}' — must be one of: {allowed}") from exc

    if node.status == StoryDeliveryNodeStatus.LOCKED and new_status != StoryDeliveryNodeStatus.LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Node {node_id} is LOCKED — its predecessor must complete first.")

    # Requirement 5 — Tech Lead review gate for Story LLD. LLD_REVIEW is
    # the dedicated review-gate node right after STORY_LLD in the default
    # sequence (see app/services/story_delivery.py); only a Tech Lead (or
    # Admin, same universal bypass every other stage-approval check in
    # this codebase grants — see app/services/permissions.py) may
    # complete it, i.e. approve the Story LLD. Requirement 6 falls out of
    # this for free: IMPLEMENTATION only unlocks once LLD_REVIEW
    # completes, via advance_lane below — no separate wiring needed.
    if node.node_key == "LLD_REVIEW" and new_status == StoryDeliveryNodeStatus.COMPLETED and actor.role not in (UserRole.TECH_LEAD, UserRole.ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Role {actor.role.value} may not approve Story LLD (LLD_REVIEW) — Tech Lead only."
        )

    # Story-level testing workflow, requirement 6 — "QA Approval requires
    # test evidence." QA_APPROVAL is the dedicated review-gate node right
    # after TESTING (mirrors LLD_REVIEW's own role gate above); completing
    # it requires both a QA (or Admin) actor AND a non-empty "Test
    # Evidence" section in the latest story_test_report StoryArtifact —
    # the same structural check GraphEngineService.validate_evidence_requirement
    # already does for the project-level testing stage, applied here to a
    # StoryArtifact instead of an ArtifactVersion.
    if node.node_key == "QA_APPROVAL" and new_status == StoryDeliveryNodeStatus.COMPLETED:
        if actor.role not in (UserRole.QA, UserRole.ADMIN):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role {actor.role.value} may not grant QA Approval — QA only.")
        test_report = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == node.lane.story_id, StoryArtifact.artifact_type == STORY_TEST_REPORT_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        evidence_error = _require_test_evidence(test_report)
        if evidence_error:
            raise HTTPException(status.HTTP_409_CONFLICT, evidence_error)

    # Story Implementation Plan Agent, rule 4 — "Implementation cannot
    # start before this plan is approved or accepted by assigned user."
    # Unlike LLD_REVIEW/QA_APPROVAL, this stage's review is its own node
    # (not a separate one right after it) — completing IMPLEMENTATION_PLAN
    # IS acceptance. Gated to whoever the node is assigned to (if set) or
    # a Tech Lead/Admin (the role this node is advisory-assigned to — see
    # _NODE_ASSIGNED_ROLE["IMPLEMENTATION_PLAN"] in
    # app/services/story_delivery.py); a real plan must exist to accept
    # (an empty/undrafted stage can't be "approved"). "Request Changes" is
    # not a separate action — it's this same endpoint with status=BLOCKED
    # and a blocked_reason, exactly like every other node's rejection path.
    if node.node_key == "IMPLEMENTATION_PLAN" and new_status == StoryDeliveryNodeStatus.COMPLETED:
        is_assignee = node.assigned_user_id is not None and node.assigned_user_id == actor.id
        if not is_assignee and actor.role not in (UserRole.TECH_LEAD, UserRole.ADMIN):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role {actor.role.value} may not accept this Implementation Plan — only its assigned user or a Tech Lead may.",
            )
        plan_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == node.lane.story_id, StoryArtifact.artifact_type == STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        if plan_artifact is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cannot accept — no Implementation Plan has been drafted for this story yet.")

    # Story Test Scenario Agent, rule 3 — "QA or Tech Lead can
    # review/approve test scenarios." Same review-is-this-node's-own-
    # completion pattern as IMPLEMENTATION_PLAN above, but role-only (no
    # assignee override — the rule names two roles, not "or whoever it's
    # assigned to"); "Request Changes" is likewise the same endpoint with
    # status=BLOCKED.
    if node.node_key == "TEST_SCENARIOS" and new_status == StoryDeliveryNodeStatus.COMPLETED and actor.role not in (UserRole.QA, UserRole.TECH_LEAD, UserRole.ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Role {actor.role.value} may not approve these Test Scenarios — QA or Tech Lead only."
        )

    # GitHub PR Review Agent, UI rule 4 — "Add button: Mark Human Review
    # Complete." HUMAN_CODE_REVIEW is the lane's real, external-to-this-app
    # GitHub PR review gate (see pr_review_run.py's docstring); completing
    # it via this same generic endpoint IS that button. Same role gate as
    # LLD_REVIEW above — Tech Lead (or Admin) only.
    if node.node_key == "HUMAN_CODE_REVIEW" and new_status == StoryDeliveryNodeStatus.COMPLETED and actor.role not in (UserRole.TECH_LEAD, UserRole.ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Role {actor.role.value} may not mark Human Code Review complete — Tech Lead only."
        )

    # HARDENING FIX — "Human approval is required before final Done."
    # RELEASE_READY is this lane's terminal node; completing it is what
    # marks the story DONE (see advance_lane below). Until now nothing
    # gated who could complete it — any actor, any role, could mark a
    # story DONE with a single PATCH. Fixed the same way as every other
    # review gate in this lane (LLD_REVIEW/QA_APPROVAL above): only the
    # role this node is already advisory-assigned to
    # (_NODE_ASSIGNED_ROLE["RELEASE_READY"] = PRODUCT_OWNER, see
    # app/services/story_delivery.py) or Admin may complete it.
    if node.node_key == "RELEASE_READY" and new_status == StoryDeliveryNodeStatus.COMPLETED and actor.role not in (UserRole.PRODUCT_OWNER, UserRole.ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Role {actor.role.value} may not mark this story Done (RELEASE_READY) — Product Owner only."
        )

    # Final Done gate — see app/services/story_done_gate.py. Re-checks all
    # 9 conditions directly against their source of truth, one last time,
    # rather than trusting the lane's own node sequence alone.
    if node.node_key == "RELEASE_READY" and new_status == StoryDeliveryNodeStatus.COMPLETED:
        unmet = evaluate_story_done_gate(db, story=db.get(Story, node.lane.story_id), lane=node.lane)
        if unmet:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cannot mark this story Done yet: " + "; ".join(unmet))

    if payload.assigned_user_id is not None:
        assigned_user = db.get(User, payload.assigned_user_id)
        if assigned_user is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"assigned_user_id {payload.assigned_user_id} does not match an existing user")
        node.assigned_user_id = assigned_user.id

    previous_status = node.status
    node.status = new_status
    node.blocked_reason = payload.blocked_reason if new_status == StoryDeliveryNodeStatus.BLOCKED else None
    if new_status == StoryDeliveryNodeStatus.IN_PROGRESS and node.started_at is None:
        node.started_at = datetime.now(timezone.utc)
    if new_status == StoryDeliveryNodeStatus.COMPLETED:
        node.completed_at = datetime.now(timezone.utc)
    db.flush()

    lane = node.lane
    story = db.get(Story, lane.story_id)
    _log_story_activity(
        db, story=story, action="story_lane_node.status_changed", actor=actor, lane_id=lane.id, node_id=node.id,
        details={"node_key": node.node_key, "from": previous_status.value, "to": new_status.value},
    )
    if node.node_key == "IMPLEMENTATION_PLAN":
        if new_status == StoryDeliveryNodeStatus.COMPLETED:
            _log_story_activity(db, story=story, action="story_implementation_plan.accepted", actor=actor, lane_id=lane.id, node_id=node.id)
        elif new_status == StoryDeliveryNodeStatus.BLOCKED:
            _log_story_activity(
                db, story=story, action="story_implementation_plan.changes_requested", actor=actor, lane_id=lane.id, node_id=node.id,
                details={"reason": payload.blocked_reason},
            )
    if node.node_key == "TEST_SCENARIOS":
        if new_status == StoryDeliveryNodeStatus.COMPLETED:
            _log_story_activity(db, story=story, action="story_test_scenarios.approved", actor=actor, lane_id=lane.id, node_id=node.id)
        elif new_status == StoryDeliveryNodeStatus.BLOCKED:
            _log_story_activity(
                db, story=story, action="story_test_scenarios.changes_requested", actor=actor, lane_id=lane.id, node_id=node.id,
                details={"reason": payload.blocked_reason},
            )

    if new_status == StoryDeliveryNodeStatus.COMPLETED:
        unlocked = advance_lane(db, lane=lane, completed_node=node)
        if unlocked is not None:
            _log_story_activity(
                db, story=story, action="story_lane_node.unlocked", actor=actor, lane_id=lane.id, node_id=unlocked.id,
                details={"node_key": unlocked.node_key, "unlocked_by": node.node_key},
            )
            # Story-level implementation workflow — the moment
            # IMPLEMENTATION unlocks (i.e. Story LLD/LLD_REVIEW just
            # approved), ensure this story has its one ImplementationTask
            # ready for POST /implementation-runs to act on. Idempotent:
            # a story only ever gets exactly one.
            if unlocked.node_key == "IMPLEMENTATION":
                _ensure_story_implementation_task(db, story=story, created_by=actor)
        elif lane.status == StoryDeliveryLaneStatus.COMPLETED:
            # Final Done gate side effects — see app/services/story_done_gate.py.
            # "Update Story Lane status to DONE" is StoryDeliveryLaneStatus
            # .COMPLETED above — this lane model's own terminal status,
            # reused rather than adding a second, functionally-identical
            # enum value.
            story.status = StoryStatus.DONE
            _log_story_activity(db, story=story, action="story_lane.completed", actor=actor, lane_id=lane.id)
            record_audit_log(
                db, project_id=story.project_id, actor_user_id=actor.id, action="story.done",
                entity_type="Story", entity_id=story.id,
                extra_data={"lane_id": str(lane.id), "jira_issue_key": story.jira_issue_key},
            )
            # Optional — best-effort, never blocks marking the story DONE
            # (see try_post_story_done_comment's own docstring for why a
            # comment, not a real status write, is this integration's
            # honest ceiling).
            if try_post_story_done_comment(db, story=story):
                _log_story_activity(db, story=story, action="story.jira_done_comment_posted", actor=actor, lane_id=lane.id)

    db.commit()
    db.refresh(node)
    return node


@router.post("/delivery-lane-nodes/{node_id}/draft-story-lld", response_model=DraftStoryLldResponse, status_code=status.HTTP_201_CREATED)
def draft_story_lld(node_id: uuid.UUID, payload: DraftStoryLldRequest, db: Session = Depends(get_db)) -> DraftStoryLldResponse:
    """Requirement 1/3/4 — drafts the STORY_LLD node's real output. See
    app/services/story_lld_agent.py for the three preconditions and the
    14-section output shape."""
    node = db.get(StoryDeliveryNode, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Delivery lane node {node_id} not found")
    if node.node_key != "STORY_LLD":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Node {node_id} is '{node.node_key}', not STORY_LLD.")
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "story_lld")

    try:
        result = run_story_lld_agent(db, node=node, triggered_by=triggered_by)
    except StoryLldError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    lane = node.lane
    story = db.get(Story, lane.story_id)
    _log_story_activity(
        db, story=story, action="story_lld.drafted", actor=triggered_by, lane_id=lane.id, node_id=node.id,
        details={"needs_clarification": result.needs_clarification, "used_mock": result.agent_run_used_mock},
    )

    db.commit()
    db.refresh(node)
    return DraftStoryLldResponse(
        needs_clarification=result.needs_clarification,
        story_artifact=StoryArtifactRead.model_validate(result.story_artifact) if result.story_artifact else None,
        node_status=node.status.value,
    )


@router.get("/stories/{story_id}/lld", response_model=StoryArtifactRead)
def get_story_lld(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryArtifact:
    """The latest STORY_LLD StoryArtifact for this story, if one has been
    drafted yet — see app/services/story_lld_agent.STORY_LLD_ARTIFACT_TYPE."""
    story = _get_story_or_404(db, story_id)
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_LLD_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} has no Story LLD drafted yet.")
    return artifact


@router.post("/delivery-lane-nodes/{node_id}/draft-implementation-plan", response_model=DraftStoryImplementationPlanResponse)
def draft_story_implementation_plan(
    node_id: uuid.UUID, payload: DraftStoryImplementationPlanRequest, db: Session = Depends(get_db)
) -> DraftStoryImplementationPlanResponse:
    """Drafts the IMPLEMENTATION_PLAN node's real output. See
    app/services/story_implementation_plan_agent.py for the preconditions
    and the 12-section output shape. Drafting never completes the node —
    see update_lane_node_status's IMPLEMENTATION_PLAN branch for the
    separate Accept/Request Changes action."""
    node = db.get(StoryDeliveryNode, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Delivery lane node {node_id} not found")
    if node.node_key != "IMPLEMENTATION_PLAN":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Node {node_id} is '{node.node_key}', not IMPLEMENTATION_PLAN.")
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "story_implementation_plan")

    try:
        result = run_story_implementation_plan_agent(db, node=node, triggered_by=triggered_by)
    except StoryImplementationPlanError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    lane = node.lane
    story = db.get(Story, lane.story_id)
    _log_story_activity(
        db, story=story, action="story_implementation_plan.drafted", actor=triggered_by, lane_id=lane.id, node_id=node.id,
        details={"needs_clarification": result.needs_clarification, "used_mock": result.agent_run_used_mock},
    )

    db.commit()
    db.refresh(node)
    return DraftStoryImplementationPlanResponse(
        needs_clarification=result.needs_clarification,
        story_artifact=StoryArtifactRead.model_validate(result.story_artifact) if result.story_artifact else None,
        node_status=node.status.value,
    )


@router.get("/stories/{story_id}/implementation-plan", response_model=StoryArtifactRead)
def get_story_implementation_plan(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryArtifact:
    """The latest STORY_IMPLEMENTATION_PLAN StoryArtifact for this story,
    if one has been drafted yet."""
    story = _get_story_or_404(db, story_id)
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} has no Implementation Plan drafted yet.")
    return artifact


@router.post("/delivery-lane-nodes/{node_id}/draft-test-scenarios", response_model=DraftStoryTestScenariosResponse)
def draft_story_test_scenarios(
    node_id: uuid.UUID, payload: DraftStoryTestScenariosRequest, db: Session = Depends(get_db)
) -> DraftStoryTestScenariosResponse:
    """Drafts the TEST_SCENARIOS node's real output. See
    app/services/story_test_scenarios_agent.py for the preconditions and
    the 10-section output shape. Drafting never completes the node —
    see update_lane_node_status's TEST_SCENARIOS branch for the separate
    review/approve action."""
    node = db.get(StoryDeliveryNode, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Delivery lane node {node_id} not found")
    if node.node_key != "TEST_SCENARIOS":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Node {node_id} is '{node.node_key}', not TEST_SCENARIOS.")
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(triggered_by, "story_test_scenarios")

    try:
        result = run_story_test_scenarios_agent(db, node=node, triggered_by=triggered_by)
    except StoryTestScenariosError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    lane = node.lane
    story = db.get(Story, lane.story_id)
    _log_story_activity(
        db, story=story, action="story_test_scenarios.drafted", actor=triggered_by, lane_id=lane.id, node_id=node.id,
        details={"needs_clarification": result.needs_clarification, "used_mock": result.agent_run_used_mock},
    )

    db.commit()
    db.refresh(node)
    return DraftStoryTestScenariosResponse(
        needs_clarification=result.needs_clarification,
        story_artifact=StoryArtifactRead.model_validate(result.story_artifact) if result.story_artifact else None,
        node_status=node.status.value,
    )


@router.get("/stories/{story_id}/test-scenarios", response_model=StoryArtifactRead)
def get_story_test_scenarios(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryArtifact:
    """The latest STORY_TEST_SCENARIOS StoryArtifact for this story, if
    one has been drafted yet."""
    story = _get_story_or_404(db, story_id)
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_TEST_SCENARIOS_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} has no Test Scenarios drafted yet.")
    return artifact


@router.get("/stories/{story_id}/implementation-task", response_model=ImplementationTaskRead)
def get_story_implementation_task(story_id: uuid.UUID, db: Session = Depends(get_db)) -> ImplementationTask:
    """Story-level implementation workflow, requirement 1 — this story's
    one ImplementationTask (see _ensure_story_implementation_task, which
    creates it the moment Story LLD/LLD_REVIEW is approved)."""
    story = _get_story_or_404(db, story_id)
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).first()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} has no implementation task yet.")
    return task


@router.get("/stories/{story_id}/test-report", response_model=StoryArtifactRead)
def get_story_test_report(story_id: uuid.UUID, db: Session = Depends(get_db)) -> StoryArtifact:
    """Story-level testing workflow, requirement 7 — the latest
    story_test_report StoryArtifact for this story, if one has been
    generated yet (see app/services/testing_agent.STORY_TEST_REPORT_ARTIFACT_TYPE)."""
    story = _get_story_or_404(db, story_id)
    artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_TEST_REPORT_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} has no test report yet.")
    return artifact


# Sprint/SprintStory routes live in app/api/routes/sprints.py.
