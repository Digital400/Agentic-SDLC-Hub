"""Route-level tests for:
  - GET /projects/{id}/stories/recommended-order
  - GET /stories/{id}/backlog-diff, POST /stories/{id}/apply-backlog-diff

sync-from-backlog is deliberately additive-only (never touches an already-
synced Story) — these two routes are the explicit, human-reviewed way a
correction made in a regenerated/re-approved backlog can still reach an
already-synced story, without ever auto-applying anything or touching its
delivery lane.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.api.routes.stories import (
    apply_story_backlog_diff,
    create_story_lane,
    get_recommended_story_order,
    get_story_backlog_diff,
    sync_stories_from_backlog,
)
from app.models import ArtifactStatus, ArtifactVersion, Story, User, UserRole
from app.schemas.story import ApplyStoryBacklogDiffRequest, CreateStoryLaneRequest, SyncStoriesFromBacklogRequest
from app.models.enums import StoryType
from tests.conftest import make_approved_artifact, make_node

BACKLOG_V1 = (
    "## Story: Scaffold the database\n\n**Epic:** Foundation\n**User Story:** As a dev, I want a schema.\n"
    "**Priority:** High\n**Dependencies:** None.\n**Acceptance Criteria:**\n- [ ] Tables exist\n\n"
    "## Story: Add user authentication\n\n**Epic:** Access\n**User Story:** As a user, I want to log in.\n"
    "**Priority:** High\n**Dependencies:** Scaffold the database\n**Acceptance Criteria:**\n- [ ] Login works\n"
)

# "Scaffold the database" corrected: new acceptance criteria + priority dropped to Medium.
BACKLOG_V2 = (
    "## Story: Scaffold the database\n\n**Epic:** Foundation\n**User Story:** As a dev, I want a schema.\n"
    "**Priority:** Medium\n**Dependencies:** None.\n**Acceptance Criteria:**\n- [ ] Tables exist\n- [ ] Migrations run in CI\n\n"
    "## Story: Add user authentication\n\n**Epic:** Access\n**User Story:** As a user, I want to log in.\n"
    "**Priority:** High\n**Dependencies:** Scaffold the database\n**Acceptance Criteria:**\n- [ ] Login works\n"
)


def _dev(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _synced(db, project, actor, content=BACKLOG_V1):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=content)
    sync_stories_from_backlog(project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=actor.id), db)
    return node


def _reapprove(db, project, node, actor, content):
    """Simulates Story Crafting being re-run and re-approved — a second
    approved Artifact row with a later updated_at, deterministically after
    whatever's already there (sqlite's naive-datetime/second-level
    timestamp precision in a fast in-memory test can otherwise tie with
    the first one, making "the latest approved" ambiguous)."""
    from app.models import Artifact

    latest = db.query(Artifact).filter(Artifact.workflow_node_id == node.id).order_by(Artifact.updated_at.desc()).first()
    new_artifact = make_approved_artifact(db, project, node, actor, content=content)
    new_artifact.updated_at = latest.updated_at + timedelta(seconds=1)
    db.flush()
    return new_artifact


# --- Recommended order ----------------------------------------------------------------------


def test_recommended_order_two_waves_from_a_simple_dependency(db, project, actor):
    _synced(db, project, actor)
    result = get_recommended_story_order(project.id, db)
    assert [w.stories[0].title for w in result.waves] == ["Scaffold the database", "Add user authentication"]
    assert result.unresolved_dependencies == {} and result.circular == []


def test_recommended_order_with_no_stories_is_empty(db, project):
    result = get_recommended_story_order(project.id, db)
    assert result.waves == [] and result.circular == []


# --- Backlog diff ----------------------------------------------------------------------------


def test_backlog_diff_reports_nothing_when_unchanged(db, project, actor):
    _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    diff = get_story_backlog_diff(story.id, db)
    assert diff.found_in_backlog and diff.up_to_date and diff.changes == []


def test_backlog_diff_reports_changed_fields_after_backlog_is_corrected(db, project, actor):
    node = _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()

    # Story Crafting re-run/corrected and re-approved — a NEW approved version.
    _reapprove(db, project, node, actor, content=BACKLOG_V2)

    diff = get_story_backlog_diff(story.id, db)
    assert diff.found_in_backlog and not diff.up_to_date
    changed_fields = {c.field for c in diff.changes}
    assert changed_fields == {"priority", "acceptance_criteria"}
    priority_change = next(c for c in diff.changes if c.field == "priority")
    assert priority_change.current == "High" and priority_change.proposed == "Medium"


def test_backlog_diff_reports_lane_active(db, project, actor):
    _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    assert get_story_backlog_diff(story.id, db).lane_active is False

    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    assert get_story_backlog_diff(story.id, db).lane_active is True


def test_backlog_diff_not_found_when_story_no_longer_in_backlog(db, project, actor):
    node = _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    _reapprove(db, project, node, actor, content="## Story: A Totally Different Story\n\n**Priority:** Low\n")

    diff = get_story_backlog_diff(story.id, db)
    assert diff.found_in_backlog is False and diff.changes == []


# --- Applying a diff -------------------------------------------------------------------------


def test_apply_backlog_diff_updates_only_requested_fields(db, project, actor):
    node = _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    _reapprove(db, project, node, actor, content=BACKLOG_V2)

    updated = apply_story_backlog_diff(story.id, ApplyStoryBacklogDiffRequest(triggered_by_user_id=actor.id, fields=["priority"]), db)

    assert updated.priority == "Medium"
    assert updated.acceptance_criteria == ["Tables exist"]  # untouched — not in the requested field list

    diff_after = get_story_backlog_diff(story.id, db)
    assert {c.field for c in diff_after.changes} == {"acceptance_criteria"}


def test_apply_backlog_diff_with_no_fields_applies_everything_changed(db, project, actor):
    node = _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    _reapprove(db, project, node, actor, content=BACKLOG_V2)

    updated = apply_story_backlog_diff(story.id, ApplyStoryBacklogDiffRequest(triggered_by_user_id=actor.id), db)

    assert updated.priority == "Medium"
    assert updated.acceptance_criteria == ["Tables exist", "Migrations run in CI"]
    assert get_story_backlog_diff(story.id, db).up_to_date


def test_apply_backlog_diff_never_touches_the_delivery_lane(db, project, actor):
    node = _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    _reapprove(db, project, node, actor, content=BACKLOG_V2)

    before = db.get(Story, story.id).delivery_lane.id
    apply_story_backlog_diff(story.id, ApplyStoryBacklogDiffRequest(triggered_by_user_id=actor.id), db)
    after = db.get(Story, story.id).delivery_lane.id
    assert before == after  # same lane row, unmodified by this call


def test_apply_backlog_diff_rejects_an_unknown_field(db, project, actor):
    _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    with pytest.raises(HTTPException) as exc:
        apply_story_backlog_diff(story.id, ApplyStoryBacklogDiffRequest(triggered_by_user_id=actor.id, fields=["not_a_real_field"]), db)
    assert exc.value.status_code == 400


def test_apply_backlog_diff_404s_when_story_not_in_current_backlog(db, project, actor):
    node = _synced(db, project, actor)
    story = db.query(Story).filter(Story.title == "Scaffold the database").one()
    _reapprove(db, project, node, actor, content="## Story: A Totally Different Story\n\n**Priority:** Low\n")
    with pytest.raises(HTTPException) as exc:
        apply_story_backlog_diff(story.id, ApplyStoryBacklogDiffRequest(triggered_by_user_id=actor.id), db)
    assert exc.value.status_code == 409
