"""Release planning from story delivery lanes — app/api/routes/releases.py,
app/models/release.py, app/models/release_story.py, and
app/services/release_notes.py.

Covers: only RELEASE_READY stories can be added (requirement 2); a
release only ever contains explicitly selected stories (requirement 1);
release notes are generated from story/PR/test-report/risk content
(requirement 6); the approval gate refuses an empty/unready/note-less
release and only succeeds once every checklist item passes
(requirement 7).
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.releases import (
    add_story_to_release,
    approve_release,
    create_release,
    generate_release_notes,
    get_release_approval_checklist,
    get_release_board,
    list_release_ready_stories,
    remove_story_from_release,
)
from app.api.routes.stories import create_story, create_story_lane, list_lane_nodes, update_lane_node_status
from app.models import ReleaseStatus, StoryArtifact, StoryDeliveryNodeStatus, StoryType, User, UserRole
from app.schemas.release import (
    AddStoryToReleaseRequest,
    GenerateReleaseNotesRequest,
    ReleaseCreate,
    ReleaseLifecycleRequest,
    RemoveStoryFromReleaseRequest,
)
from app.schemas.story import CreateStoryLaneRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services.story_delivery import DEFAULT_STORY_DELIVERY_NODES

from tests.conftest import make_approved_artifact, make_node


def _approved_project(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: Placeholder\n")


def _create_direct_story(db, project, creator, *, title: str = "Add password reset endpoint"):
    return create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user, I want...", created_by_id=creator.id),
        db,
    )


def _drive_lane_to_release_ready(db, project, story, actor):
    """Runs a story's delivery lane all the way to RELEASE_READY
    (COMPLETED) — same driving loop as
    test_story_delivery_lane.py's own final-node test, including the
    QA_APPROVAL evidence fixture."""
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)

    from app.api.routes.stories import get_story_delivery_lane

    lane = get_story_delivery_lane(story.id, db)
    for _ in range(len(DEFAULT_STORY_DELIVERY_NODES)):
        nodes = list_lane_nodes(lane.id, db)
        current = next(n for n in nodes if n.status == StoryDeliveryNodeStatus.READY)
        if current.node_key == "QA_APPROVAL":
            db.add(
                StoryArtifact(
                    story_id=story.id, lane_id=lane.id, node_id=current.id, artifact_type="story_test_report",
                    title="Test Report", content_markdown="## Test Evidence\nPass: 1 · Fail: 0\n", version_number=1,
                    created_by_id=actor.id,
                )
            )
            db.flush()
        elif current.node_key == "IMPLEMENTATION_PLAN":
            db.add(
                StoryArtifact(
                    story_id=story.id, lane_id=lane.id, node_id=current.id, artifact_type="story_implementation_plan",
                    title="Implementation Plan", content_markdown="## Implementation Summary\nPlan.\n", version_number=1,
                    created_by_id=actor.id,
                )
            )
            db.flush()
        update_lane_node_status(current.id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)


# --- Adding stories (requirements 1, 2) -------------------------------------------------


def test_release_ready_story_can_be_added(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Sprint 1 Release", version="1.0.0", created_by_id=actor.id), db)
    release_story = add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)

    assert release_story.story_id == story.id
    board = get_release_board(release.id, db)
    assert len(board.items) == 1
    assert board.items[0].story.id == story.id


def test_story_not_release_ready_cannot_be_added(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)  # lane exists but hasn't progressed

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_story_with_no_lane_at_all_cannot_be_added(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_release_only_contains_explicitly_added_stories(db, project, actor):
    """Requirement 1 — no implicit "add everything ready"."""
    _approved_project(db, project, actor)
    story_a = _create_direct_story(db, project, actor, title="Story A")
    story_b = _create_direct_story(db, project, actor, title="Story B")
    _drive_lane_to_release_ready(db, project, story_a, actor)
    _drive_lane_to_release_ready(db, project, story_b, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story_a.id, actor_user_id=actor.id), db)

    board = get_release_board(release.id, db)
    assert [i.story.id for i in board.items] == [story_a.id]

    ready = list_release_ready_stories(project.id, db)
    assert {s.id for s in ready} == {story_a.id, story_b.id}  # both ready, only one selected


def test_removing_a_story_from_a_release(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)
    remove_story_from_release(release.id, story.id, RemoveStoryFromReleaseRequest(actor_user_id=actor.id), db)

    board = get_release_board(release.id, db)
    assert board.items == []


# --- Release notes generation (requirement 6) --------------------------------------------


def test_generate_release_notes_includes_story_and_risk_content(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor, title="Password Reset")
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)

    updated = generate_release_notes(release.id, GenerateReleaseNotesRequest(triggered_by_user_id=actor.id), db)

    assert "Password Reset" in updated.release_notes
    assert "Story Summaries" in updated.release_notes
    assert "Test Reports" in updated.release_notes
    assert "Test Evidence" in updated.release_notes  # from the fixture's story_test_report content


# --- Approval gate (requirement 7) --------------------------------------------------------


def test_approval_blocked_with_no_stories(db, project, actor):
    _approved_project(db, project, actor)
    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)

    checklist = get_release_approval_checklist(release.id, db)
    assert checklist  # non-empty — not approvable yet

    with pytest.raises(HTTPException) as exc_info:
        approve_release(release.id, ReleaseLifecycleRequest(actor_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_approval_blocked_without_generated_notes(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        approve_release(release.id, ReleaseLifecycleRequest(actor_user_id=actor.id), db)
    assert exc_info.value.status_code == 409


def test_approval_succeeds_once_checklist_passes(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)
    generate_release_notes(release.id, GenerateReleaseNotesRequest(triggered_by_user_id=actor.id), db)

    assert get_release_approval_checklist(release.id, db) == []

    approved = approve_release(release.id, ReleaseLifecycleRequest(actor_user_id=actor.id), db)
    assert approved.status == ReleaseStatus.APPROVED
    assert approved.approved_by_id == actor.id
    assert approved.approved_at is not None


def test_a_non_approver_role_cannot_approve(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)
    generate_release_notes(release.id, GenerateReleaseNotesRequest(triggered_by_user_id=actor.id), db)

    developer = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(developer)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        approve_release(release.id, ReleaseLifecycleRequest(actor_user_id=developer.id), db)
    assert exc_info.value.status_code == 403


def test_cannot_approve_twice(db, project, actor):
    _approved_project(db, project, actor)
    story = _create_direct_story(db, project, actor)
    _drive_lane_to_release_ready(db, project, story, actor)

    release = create_release(ReleaseCreate(project_id=project.id, name="Release", version="1.0.0", created_by_id=actor.id), db)
    add_story_to_release(release.id, AddStoryToReleaseRequest(story_id=story.id, actor_user_id=actor.id), db)
    generate_release_notes(release.id, GenerateReleaseNotesRequest(triggered_by_user_id=actor.id), db)
    approve_release(release.id, ReleaseLifecycleRequest(actor_user_id=actor.id), db)

    with pytest.raises(HTTPException) as exc_info:
        approve_release(release.id, ReleaseLifecycleRequest(actor_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
