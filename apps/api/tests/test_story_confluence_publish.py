"""Story-scoped Confluence publishing — app/services/story_confluence_publish.py
and app/api/routes/confluence_integration.py's /confluence/stories/{id}/...
routes. The per-story sibling of the project-level publish flow covered
in test_confluence_routes.py.

Covers: a story's Story LLD can be previewed/published to Confluence only
once its LLD_REVIEW has been approved; publishing creates a page the
first time and updates the same page on a second publish; a project-level
page and a story-level page for the same artifact_type never collide
(the partial-unique-index fix); the token never leaks.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.confluence_integration import (
    create_confluence_space_link,
    get_story_confluence_publish_preview,
    publish_story_to_confluence,
)
from app.api.routes.stories import create_story, create_story_lane, draft_story_lld, update_lane_node_status
from app.models import (
    AgentDefinition,
    ConfluencePageLink,
    Story,
    StoryDeliveryLane,
    StoryType,
    User,
    UserRole,
)
from app.schemas.confluence_integration import StoryConfluencePublishRequest
from app.schemas.story import CreateStoryLaneRequest, DraftStoryLldRequest, StoryCreate, UpdateLaneNodeStatusRequest
from app.services import ai_generation
from app.services.story_lld_agent import STORY_LLD_AGENT_KEY
from tests.conftest import make_agent_prompt, make_approved_artifact, make_node
from tests.test_confluence_routes import REAL_TOKEN, _connect_and_link, _mock_confluence

SAMPLE_HLD = "# HLD\n\n## Architecture\nSome design.\n"


@pytest.fixture(autouse=True)
def _force_mock_provider(monkeypatch):
    monkeypatch.setattr(ai_generation, "get_active_provider", lambda: "mock")


@pytest.fixture(autouse=True)
def _story_lld_agent(db):
    agent = AgentDefinition(agent_key=STORY_LLD_AGENT_KEY, name="Story LLD Agent", model_name="mock")
    db.add(agent)
    db.flush()
    make_agent_prompt(db, stage="story_lld", agent=agent)


def _tech_lead(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Tech Lead", role=UserRole.TECH_LEAD)
    db.add(user)
    db.flush()
    return user


def _story_with_approved_lld(db, project, actor, *, title: str = "Add reset endpoint"):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)

    story = create_story(
        StoryCreate(project_id=project.id, title=title, mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    story_row = db.get(Story, story.id)
    lane = story_row.delivery_lane
    nodes = {n.node_key: n for n in lane.nodes}

    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)
    tech_lead = _tech_lead(db)
    update_lane_node_status(nodes["LLD_REVIEW"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=tech_lead.id), db)

    return story_row, lane


def test_preview_blocks_when_lld_review_not_yet_approved(db, project, actor, monkeypatch):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    make_approved_artifact(db, project, node, actor, content="## Story: X\n")
    hld_node = make_node(db, project, node_key="hld", order_index=1, output_artifact_type="hld_document")
    make_approved_artifact(db, project, hld_node, actor, content=SAMPLE_HLD)
    story = create_story(
        StoryCreate(project_id=project.id, title="Unreviewed", mode=StoryType.VERTICAL, user_story="As a user...", created_by_id=actor.id), db,
    )
    create_story_lane(story.id, CreateStoryLaneRequest(triggered_by_user_id=actor.id), db)
    story_row = db.get(Story, story.id)
    nodes = {n.node_key: n for n in story_row.delivery_lane.nodes}
    update_lane_node_status(nodes["STORY_READY"].id, UpdateLaneNodeStatusRequest(status="COMPLETED", actor_user_id=actor.id), db)
    draft_story_lld(nodes["STORY_LLD"].id, DraftStoryLldRequest(triggered_by_user_id=actor.id), db)

    _connect_and_link(db, actor, project, monkeypatch)
    preview = get_story_confluence_publish_preview(story_row.id, db)

    item = preview.items[0]
    assert not item.validation_errors == []
    assert "not approved yet" in item.validation_errors[0]


def test_publish_creates_then_updates_the_same_page(db, project, actor, monkeypatch):
    story_row, lane = _story_with_approved_lld(db, project, actor)
    _connect_and_link(db, actor, project, monkeypatch)
    pages = _mock_confluence(monkeypatch)

    preview = get_story_confluence_publish_preview(story_row.id, db)
    assert preview.items[0].is_valid if hasattr(preview.items[0], "is_valid") else not preview.items[0].validation_errors

    response = publish_story_to_confluence(
        story_row.id, StoryConfluencePublishRequest(triggered_by_user_id=actor.id, artifact_types=["story_lld"]), db
    )
    assert response.results[0].status == "published"
    assert len(pages) == 1

    link = db.query(ConfluencePageLink).filter(ConfluencePageLink.story_id == story_row.id).first()
    assert link is not None
    assert link.confluence_page_version == 1

    # Second publish updates the same page — no new page created.
    response_2 = publish_story_to_confluence(
        story_row.id, StoryConfluencePublishRequest(triggered_by_user_id=actor.id, artifact_types=["story_lld"]), db
    )
    assert response_2.results[0].status == "updated"
    assert len(pages) == 1
    db.refresh(link)
    assert link.confluence_page_version == 2


def test_project_level_and_story_level_pages_never_collide(db, project, actor, monkeypatch):
    """Regression guard for the partial-unique-index fix — a project-level
    hld_document page and this story's own story_lld page must coexist
    without violating any unique constraint, even though both rows share
    the same confluence_space_link_id."""
    from app.api.routes.confluence_integration import publish_to_confluence
    from app.schemas.confluence_integration import ConfluencePublishRequest

    story_row, lane = _story_with_approved_lld(db, project, actor)
    _mock_confluence(monkeypatch)
    _connect_and_link(db, actor, project, monkeypatch)

    # Project-level HLD publish (hld_document is already APPROVED above).
    project_result = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=actor.id, artifact_types=["hld_document"]), db
    )
    assert project_result.results[0].status == "published"

    story_result = publish_story_to_confluence(
        story_row.id, StoryConfluencePublishRequest(triggered_by_user_id=actor.id, artifact_types=["story_lld"]), db
    )
    assert story_result.results[0].status == "published"

    links = db.query(ConfluencePageLink).filter(ConfluencePageLink.confluence_space_link_id.isnot(None)).all()
    assert len(links) == 2


def test_publish_never_leaks_the_token_on_failure(db, project, actor, monkeypatch):
    story_row, lane = _story_with_approved_lld(db, project, actor)
    _connect_and_link(db, actor, project, monkeypatch)

    import app.api.routes.confluence_integration as confluence_routes
    from app.services.confluence_integration import ConfluenceIntegrationError

    def _fail(*a, **kw):
        raise ConfluenceIntegrationError("boom")

    monkeypatch.setattr(confluence_routes.confluence_api, "create_page", _fail)

    response = publish_story_to_confluence(
        story_row.id, StoryConfluencePublishRequest(triggered_by_user_id=actor.id, artifact_types=["story_lld"]), db
    )
    assert response.results[0].status == "failed"
    assert REAL_TOKEN not in str(response.results[0].errors)
