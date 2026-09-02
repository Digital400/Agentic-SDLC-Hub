"""Story Crafting's expanded VERTICAL/HORIZONTAL output fields — see
app/db/seed.py's story_crafting prompt and app/services/story_export.py's
parser. Covers: the new fields parse correctly, and sync-from-backlog
persists them onto the real Story row (preferring the agent's own
Suggested Owner Role over the keyword-heuristic fallback, and only
seeding story_points when the estimate parses as a plain integer)."""

from app.api.routes.stories import sync_stories_from_backlog
from app.models import User, UserRole
from app.schemas.story import SyncStoriesFromBacklogRequest
from app.services.story_export import parse_story_backlog
from app.models.enums import StoryType
from tests.conftest import make_approved_artifact, make_node

FULL_STORY_BLOCK = """## Story: Add password reset endpoint
**Epic:** Account Recovery
**Feature:** Password Reset
**Mode:** VERTICAL
**User Story:** As a user, I want to reset my password, so that I can regain access to my account.
**Business Value:** Reduces support tickets from locked-out users.
**Acceptance Criteria:**
- [ ] A reset link is emailed to the user
- [ ] The link expires after 1 hour
**Suggested Owner Role:** DEVELOPER
**Technical Areas Involved:** Backend, Database
**Dependencies:** None.
**Priority:** High
**Story Points Estimate:** 5
**Jira Issue Type:** Story
**Suggested Subtasks:**
- [ ] Add reset-token table
- [ ] Add POST /auth/password-reset endpoint
**Release Readiness Criteria:**
- [ ] Endpoint is behind rate limiting
**Definition of Done:**
- [ ] Code reviewed and merged
"""

MALFORMED_STORY_BLOCK = """## Story: Ambiguous points story
**Epic:** Misc
**Feature:** Misc
**User Story:** As a user, I want something, so that something happens.
**Priority:** Low
**Story Points Estimate:** M (medium)
**Acceptance Criteria:**
- [ ] Works
"""


def _ba(db) -> User:
    user = User(email="ba@example.com", full_name="BA", role=UserRole.BA)
    db.add(user)
    db.flush()
    return user


def test_parse_story_backlog_extracts_every_new_field():
    [story] = parse_story_backlog(FULL_STORY_BLOCK)
    assert story.mode == "VERTICAL"
    assert story.business_value == "Reduces support tickets from locked-out users."
    assert story.suggested_owner_role == "DEVELOPER"
    assert story.technical_areas == ["Backend", "Database"]
    assert story.story_points_estimate == "5"
    assert story.jira_issue_type == "Story"
    assert story.suggested_subtasks == ["Add reset-token table", "Add POST /auth/password-reset endpoint"]
    assert story.release_readiness_criteria == ["Endpoint is behind rate limiting"]


def test_technical_areas_accepts_comma_separated_single_line():
    [story] = parse_story_backlog(FULL_STORY_BLOCK.replace("**Technical Areas Involved:** Backend, Database", "**Technical Areas Involved:** Backend, Database, Integration"))
    assert story.technical_areas == ["Backend", "Database", "Integration"]


def test_sync_persists_agent_stated_owner_role_and_points(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=FULL_STORY_BLOCK)
    ba = _ba(db)

    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    story = result.created[0]
    assert story.suggested_owner_role == "DEVELOPER"
    assert story.story_points == 5
    assert story.business_value == "Reduces support tickets from locked-out users."
    assert story.technical_areas == ["Backend", "Database"]
    assert story.jira_issue_type == "Story"
    assert story.suggested_subtasks == ["Add reset-token table", "Add POST /auth/password-reset endpoint"]
    assert story.release_readiness_criteria == ["Endpoint is behind rate limiting"]


def test_sync_falls_back_to_heuristic_role_and_leaves_points_unset_when_unparseable(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=MALFORMED_STORY_BLOCK)
    ba = _ba(db)

    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )
    story = result.created[0]
    # No "Suggested Owner Role" field in this block — falls back to the
    # keyword heuristic, which must still produce *something* valid.
    assert story.suggested_owner_role in {"DEVELOPER", "QA", "DEVOPS", "BA"}
    # "M (medium)" doesn't parse as a plain integer — left for a human.
    assert story.story_points is None
