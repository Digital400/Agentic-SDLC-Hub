"""Release planning from story delivery lanes — see app/models/release.py
and app/models/release_story.py.

Distinct from the existing per-Sprint release_planning stage
(app/api/routes/sprints.py's generate_release_plan, a project-level
Artifact rendered from one sprint's stories): a Release here is its own
free-standing model, curated by hand from any RELEASE_READY stories
regardless of which sprint (if any) they came from.

Rules enforced here:
  1/requirement 1 — a release includes only explicitly selected stories
     (POST /releases/{id}/stories), never implicitly "everything ready".
  2 — a story can only be added once its own delivery lane has reached
     RELEASE_READY (_require_release_ready).
  7 — approval only via POST /releases/{id}/approve, which itself
     requires non-empty release notes and at least one story
     (_require_approvable).
Every mutation is audited (requirement 8).
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.routes.stories import _get_project_or_404, _get_story_or_404, _story_to_read
from app.core.database import get_db
from app.models import Release, ReleaseStatus, ReleaseStory, Story, StoryDeliveryNodeStatus, User, UserRole
from app.schemas.release import (
    AddStoryToReleaseRequest,
    GenerateReleaseNotesRequest,
    ReleaseBoardItem,
    ReleaseBoardRead,
    ReleaseCreate,
    ReleaseLifecycleRequest,
    ReleaseRead,
    ReleaseStoryRead,
    ReleaseUpdate,
    RemoveStoryFromReleaseRequest,
)
from app.schemas.story import StoryRead
from app.services.audit import record_audit_log
from app.services.permissions import require_can_approve_stage, require_can_edit_stage
from app.services.release_notes import render_release_notes

router = APIRouter(tags=["releases"])


def _get_release_or_404(db: Session, release_id: uuid.UUID) -> Release:
    release = db.get(Release, release_id)
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Release {release_id} not found")
    return release


def _require_release_editable(release: Release, actor: User) -> None:
    """A release stops accepting edits once it's left DRAFT — mirrors
    _require_sprint_editable's ADMIN-override convention in
    app/api/routes/sprints.py."""
    if release.status != ReleaseStatus.DRAFT and actor.role != UserRole.ADMIN:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Release {release.id} is {release.status.value} — only an Admin may edit it (override)."
        )


def _is_release_ready(story: Story) -> bool:
    """Requirement 2 — "only stories with RELEASE_READY status can be
    added to release." No dedicated Story.status value exists for this
    (StoryStatus tracks PENDING/IN_SPRINT/LANE_ACTIVE/DONE, not lane
    stage) — the real signal is the story's own delivery lane's
    RELEASE_READY node having reached COMPLETED, the same check
    app/services/release_export.py's _lane_stage_label already makes for
    the per-sprint release plan."""
    lane = story.delivery_lane
    if lane is None:
        return False
    release_ready_node = next((n for n in lane.nodes if n.node_key == "RELEASE_READY"), None)
    return release_ready_node is not None and release_ready_node.status == StoryDeliveryNodeStatus.COMPLETED


def _require_release_ready(story: Story) -> None:
    if not _is_release_ready(story):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Story {story.id} has not reached RELEASE_READY in its delivery lane yet — it cannot be added to a release.",
        )


def _release_stories(db: Session, release: Release) -> list[Story]:
    story_ids = [rs.story_id for rs in release.release_stories]
    if not story_ids:
        return []
    stories = db.query(Story).filter(Story.id.in_(story_ids)).all()
    by_id = {s.id: s for s in stories}
    return [by_id[sid] for sid in story_ids if sid in by_id]


@router.post("/releases", response_model=ReleaseRead, status_code=status.HTTP_201_CREATED)
def create_release(payload: ReleaseCreate, db: Session = Depends(get_db)) -> ReleaseRead:
    project = _get_project_or_404(db, payload.project_id)
    creator = db.get(User, payload.created_by_id)
    if creator is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"created_by_id {payload.created_by_id} does not match an existing user")
    require_can_edit_stage(creator, "release_planning")

    release = Release(
        project_id=project.id, name=payload.name, version=payload.version, target_date=payload.target_date,
        created_by_id=creator.id,
    )
    db.add(release)
    db.flush()

    record_audit_log(
        db, project_id=project.id, actor_user_id=creator.id, action="release.created",
        entity_type="Release", entity_id=release.id, extra_data={"name": release.name, "version": release.version},
    )

    db.commit()
    db.refresh(release)
    return ReleaseRead.model_validate(release)


@router.get("/projects/{project_id}/releases", response_model=list[ReleaseRead])
def list_releases(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[ReleaseRead]:
    _get_project_or_404(db, project_id)
    releases = db.query(Release).filter(Release.project_id == project_id).order_by(Release.created_at.desc()).all()
    return [ReleaseRead.model_validate(r) for r in releases]


@router.get("/projects/{project_id}/release-ready-stories", response_model=list[StoryRead])
def list_release_ready_stories(project_id: uuid.UUID, db: Session = Depends(get_db)) -> list[StoryRead]:
    """Requirement 5's UI — "Available release-ready stories": every
    project story whose delivery lane has reached RELEASE_READY,
    regardless of whether it's already selected into some other release
    (a story may legitimately belong to more than one in-flight release
    curation until one is actually shipped)."""
    _get_project_or_404(db, project_id)
    stories = db.query(Story).filter(Story.project_id == project_id).order_by(Story.created_at.asc()).all()
    return [_story_to_read(db, s) for s in stories if _is_release_ready(s)]


@router.patch("/releases/{release_id}", response_model=ReleaseRead)
def update_release(release_id: uuid.UUID, payload: ReleaseUpdate, db: Session = Depends(get_db)) -> ReleaseRead:
    release = _get_release_or_404(db, release_id)
    actor = db.get(User, payload.updated_by_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"updated_by_id {payload.updated_by_id} does not match an existing user")
    require_can_edit_stage(actor, "release_planning")
    _require_release_editable(release, actor)

    changed_fields: list[str] = []
    for field_name in ("name", "version", "target_date", "release_notes"):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(release, field_name, value)
            changed_fields.append(field_name)

    db.flush()
    if changed_fields:
        record_audit_log(
            db, project_id=release.project_id, actor_user_id=actor.id, action="release.updated",
            entity_type="Release", entity_id=release.id, extra_data={"fields": changed_fields},
        )

    db.commit()
    db.refresh(release)
    return ReleaseRead.model_validate(release)


@router.get("/releases/{release_id}/board", response_model=ReleaseBoardRead)
def get_release_board(release_id: uuid.UUID, db: Session = Depends(get_db)) -> ReleaseBoardRead:
    release = _get_release_or_404(db, release_id)
    items = [
        ReleaseBoardItem(release_story=ReleaseStoryRead.model_validate(rs), story=_story_to_read(db, rs.story))
        for rs in release.release_stories
    ]
    return ReleaseBoardRead(release=ReleaseRead.model_validate(release), items=items)


@router.post("/releases/{release_id}/stories", response_model=ReleaseStoryRead, status_code=status.HTTP_201_CREATED)
def add_story_to_release(release_id: uuid.UUID, payload: AddStoryToReleaseRequest, db: Session = Depends(get_db)) -> ReleaseStory:
    release = _get_release_or_404(db, release_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "release_planning")
    _require_release_editable(release, actor)

    story = _get_story_or_404(db, payload.story_id)
    if story.project_id != release.project_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Story {story.id} does not belong to this release's project.")
    _require_release_ready(story)

    existing = db.query(ReleaseStory).filter(ReleaseStory.release_id == release.id, ReleaseStory.story_id == story.id).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Story {story.id} is already in release {release.id}.")

    release_story = ReleaseStory(release_id=release.id, story_id=story.id, added_by_id=actor.id)
    db.add(release_story)
    db.flush()

    record_audit_log(
        db, project_id=release.project_id, actor_user_id=actor.id, action="release.story_added",
        entity_type="ReleaseStory", entity_id=release_story.id,
        extra_data={"release_id": str(release.id), "story_id": str(story.id)},
    )

    db.commit()
    db.refresh(release_story)
    return release_story


@router.delete("/releases/{release_id}/stories/{story_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_story_from_release(
    release_id: uuid.UUID, story_id: uuid.UUID, payload: RemoveStoryFromReleaseRequest, db: Session = Depends(get_db)
) -> None:
    release = _get_release_or_404(db, release_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_edit_stage(actor, "release_planning")
    _require_release_editable(release, actor)

    release_story = db.query(ReleaseStory).filter(ReleaseStory.release_id == release_id, ReleaseStory.story_id == story_id).first()
    if release_story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Story {story_id} is not in release {release_id}.")

    db.delete(release_story)
    db.flush()

    record_audit_log(
        db, project_id=release.project_id, actor_user_id=actor.id, action="release.story_removed",
        entity_type="ReleaseStory", entity_id=release_story.id,
        extra_data={"release_id": str(release.id), "story_id": str(story_id)},
    )
    db.commit()


@router.post("/releases/{release_id}/generate-notes", response_model=ReleaseRead)
def generate_release_notes(release_id: uuid.UUID, payload: GenerateReleaseNotesRequest, db: Session = Depends(get_db)) -> ReleaseRead:
    """Requirement 6 — release notes generated from each selected story's
    summary, PR summary, test report, and known risks. Deterministic, not
    an AI draft (see app/services/release_notes.py); safe to call again —
    replaces `release_notes` outright each time."""
    release = _get_release_or_404(db, release_id)
    actor = db.get(User, payload.triggered_by_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")
    require_can_edit_stage(actor, "release_planning")
    _require_release_editable(release, actor)

    stories = _release_stories(db, release)
    release.release_notes = render_release_notes(db, release=release, stories=stories)
    db.flush()

    record_audit_log(
        db, project_id=release.project_id, actor_user_id=actor.id, action="release.notes_generated",
        entity_type="Release", entity_id=release.id, extra_data={"story_count": len(stories)},
    )

    db.commit()
    db.refresh(release)
    return ReleaseRead.model_validate(release)


def _require_approvable(release: Release, stories: list[Story]) -> list[str]:
    """Requirement 5's "release approval checklist" — the concrete gate
    behind it (requirement 7): every item here must be true before
    approval is allowed. Returned as a list of failures so the UI can
    render each checklist item's own pass/fail state, not just a single
    opaque error."""
    failures: list[str] = []
    if not stories:
        failures.append("At least one story must be selected.")
    if not release.release_notes.strip():
        failures.append("Release notes must be generated (or written) before approval.")
    not_ready = [s.title for s in stories if not _is_release_ready(s)]
    if not_ready:
        failures.append(f"Every selected story must still be RELEASE_READY (not ready: {', '.join(not_ready)}).")
    return failures


@router.get("/releases/{release_id}/approval-checklist", response_model=list[str])
def get_release_approval_checklist(release_id: uuid.UUID, db: Session = Depends(get_db)) -> list[str]:
    """Requirement 5 — lets the UI render the checklist (and what's still
    failing it) before the user attempts POST /approve. An empty list
    means the release is approvable right now."""
    release = _get_release_or_404(db, release_id)
    return _require_approvable(release, _release_stories(db, release))


@router.post("/releases/{release_id}/approve", response_model=ReleaseRead)
def approve_release(release_id: uuid.UUID, payload: ReleaseLifecycleRequest, db: Session = Depends(get_db)) -> ReleaseRead:
    """Requirement 7 — the approval gate. Only reachable from DRAFT, only
    by a role allowed to approve the release_planning stage, and only
    once every _require_approvable check passes."""
    release = _get_release_or_404(db, release_id)
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"actor_user_id {payload.actor_user_id} does not match an existing user")
    require_can_approve_stage(actor, "release_planning")

    if release.status != ReleaseStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Release {release_id} is {release.status.value}, not DRAFT — cannot approve it.")

    stories = _release_stories(db, release)
    failures = _require_approvable(release, stories)
    if failures:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot approve — " + "; ".join(failures))

    release.status = ReleaseStatus.APPROVED
    release.approved_by_id = actor.id
    release.approved_at = datetime.now(timezone.utc)
    db.flush()

    record_audit_log(
        db, project_id=release.project_id, actor_user_id=actor.id, action="release.approved",
        entity_type="Release", entity_id=release.id, extra_data={"story_count": len(stories)},
    )

    db.commit()
    db.refresh(release)
    return ReleaseRead.model_validate(release)
