"""app/services/sprint_export.py / release_export.py: deterministic
renderers, not AI drafts. Covers the release plan's core gate — a sprint
can only be reported "ready to release" once every included story's lane
has actually reached release_ready (COMPLETED)."""

from app.models import Sprint, StoryDeliveryLane, StoryDeliveryNode, StoryDeliveryNodeStatus
from app.services.release_export import render_release_plan_markdown
from app.services.sprint_export import render_sprint_plan_markdown
from tests.conftest import make_approved_artifact, make_node, make_story


def _sprint(db, project, actor) -> Sprint:
    sprint = Sprint(project_id=project.id, name="Sprint 1", created_by_id=actor.id)
    db.add(sprint)
    db.flush()
    return sprint


def test_sprint_plan_lists_unassigned_owners(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    artifact = make_approved_artifact(db, project, node, actor, content="## Story: A\n")
    story = make_story(db, project, actor, artifact.current_version_id, title="A")
    sprint = _sprint(db, project, actor)
    story.sprint_id = sprint.id
    db.flush()

    markdown = render_sprint_plan_markdown(sprint, [story])
    assert "A" in markdown
    assert "Unassigned" in markdown


def test_release_plan_reports_not_ready_until_release_ready_completes(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, output_artifact_type="story_backlog")
    artifact = make_approved_artifact(db, project, node, actor, content="## Story: A\n")
    story = make_story(db, project, actor, artifact.current_version_id, title="A")
    sprint = _sprint(db, project, actor)
    story.sprint_id = sprint.id
    db.flush()

    markdown = render_release_plan_markdown(db, sprint, [story])
    assert "No delivery lane yet" in markdown
    assert "Not every story" in markdown

    from datetime import datetime, timezone
    story.lane_created_at = datetime.now(timezone.utc)
    lane = StoryDeliveryLane(project_id=project.id, story_id=story.id, created_by_id=actor.id)
    db.add(lane)
    db.flush()
    release_ready_node = StoryDeliveryNode(
        lane_id=lane.id, node_key="RELEASE_READY", name="Release Ready",
        status=StoryDeliveryNodeStatus.COMPLETED, order_index=9,
    )
    db.add(release_ready_node)
    db.flush()
    lane.current_node_id = release_ready_node.id
    db.flush()
    db.expire(story, ["delivery_lane"])  # the first render above cached delivery_lane=None on this instance

    markdown_2 = render_release_plan_markdown(db, sprint, [story])
    assert "Release Ready" in markdown_2
    assert "All stories in this sprint have reached Release Ready." in markdown_2
