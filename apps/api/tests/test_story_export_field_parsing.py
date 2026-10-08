"""app/services/story_export.py's field parser (_FIELD_RE) — regression
coverage for a real production failure: a model that renders every
field as a Markdown bullet ("- **Epic:** ..." / "- **Feature:** ...")
instead of a bare "**Epic:** ..." line caused the field regex's lookahead
to never find the next field boundary, so one field (whichever came
first) swallowed the rest of the entire story block — in the field case,
over 2800 characters, which then failed Story.epic's VARCHAR(255) column
with a raw psycopg2 error surfaced to the user as an opaque "Failed to
sync stories from the approved backlog."
"""

import uuid

from app.api.routes.stories import sync_stories_from_backlog
from app.models import Story, StoryType, User, UserRole
from app.schemas.story import SyncStoriesFromBacklogRequest
from app.services.story_export import parse_story_backlog
from tests.conftest import make_approved_artifact, make_node

# Mirrors the real, observed model output that broke sync — every field
# rendered as a bulleted list item, not a bare bold-labeled line.
BULLETED_STORY_BLOCK = """## Story: S1 — User Authenticates and Lands on Their Workspace

- **Epic:** Tenant Onboarding & Identity
- **Feature:** Internal user sign-in (Entra ID OIDC)
- **Mode:** VERTICAL
- **User Story:** As an internal team member, I want to sign in with my corporate account.
- **Business Value:** Removes password management overhead for the pilot.
- **Acceptance Criteria:**
  - [ ] Sign-in redirects to the OIDC provider and back.
  - [ ] A session is established on success.
- **Suggested Owner Role:** DEVELOPER
- **Technical Areas Involved:** Backend, Frontend
- **Dependencies:** None.
- **Priority:** High
- **Story Points Estimate:** 5
- **Jira Issue Type:** Story
- **Suggested Subtasks:**
  - [ ] Wire OIDC middleware.
  - [ ] Build sign-in redirect page.
- **Release Readiness Criteria:**
  - [ ] Auth flow verified in the pilot environment.
- **Definition of Done:**
  - [ ] Tests pass in CI.

---

## Story: S2 — Second Story

- **Epic:** Tenant Onboarding & Identity
- **Feature:** Internal workspace bootstrap
- **Mode:** VERTICAL
- **User Story:** As an owner, I want to create a workspace.
- **Priority:** Medium
"""


def test_bulleted_fields_do_not_bleed_into_the_next_field():
    stories = parse_story_backlog(BULLETED_STORY_BLOCK)
    assert len(stories) == 2

    s1 = stories[0]
    assert s1.epic == "Tenant Onboarding & Identity"
    assert s1.feature == "Internal user sign-in (Entra ID OIDC)"
    assert s1.mode == "VERTICAL"
    assert "sign in with my corporate account" in s1.user_story
    assert s1.priority == "High"
    assert s1.jira_issue_type == "Story"
    assert len(s1.acceptance_criteria) == 2
    assert len(s1.suggested_subtasks) == 2

    s2 = stories[1]
    assert s2.epic == "Tenant Onboarding & Identity"
    assert s2.feature == "Internal workspace bootstrap"
    assert s2.priority == "Medium"

    # The actual failure signature: without the fix, s1.epic alone would
    # run past 2000+ characters (the entire rest of the block).
    assert len(s1.epic) < 100


def _ba(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="BA", role=UserRole.BA)
    db.add(user)
    db.flush()
    return user


def test_sync_from_backlog_succeeds_with_bulleted_field_format(db, project, actor):
    """End-to-end regression guard — this exact document, synced through
    the real route, must not raise a database error (it used to fail
    with psycopg2.errors.StringDataRightTruncation on Story.epic)."""
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content=BULLETED_STORY_BLOCK)
    ba = _ba(db)

    result = sync_stories_from_backlog(
        project.id, SyncStoriesFromBacklogRequest(story_type=StoryType.VERTICAL, triggered_by_user_id=ba.id), db
    )

    assert result.parsed_count == 2
    assert len(result.created) == 2
    row = db.get(Story, result.created[0].id)
    assert row.epic == "Tenant Onboarding & Identity"
    assert len(row.epic) <= 255
