"""Deterministic Markdown renderer for a Sprint's release plan — the
Release Planning stage's "draft". Gated, not just descriptive: a sprint's
release plan can only claim "ready to release" for a story whose own
delivery lane has actually reached RELEASE_READY (COMPLETED) — see
app/models/enums.py's StoryDeliveryNodeStatus and
app/services/story_delivery.py.
"""

from sqlalchemy.orm import Session

from app.models import Sprint, Story, StoryDeliveryNodeStatus


def _lane_stage_label(db: Session, story: Story) -> tuple[str, bool]:
    """Returns (human-readable current lane stage, is_release_ready)."""
    lane = story.delivery_lane
    if lane is None:
        return "No delivery lane yet", False

    release_ready_node = next((n for n in lane.nodes if n.node_key == "RELEASE_READY"), None)
    if release_ready_node is not None and release_ready_node.status == StoryDeliveryNodeStatus.COMPLETED:
        return "Release Ready", True

    # Otherwise report the furthest-along lane node that isn't LOCKED.
    for node in sorted(lane.nodes, key=lambda n: n.order_index, reverse=True):
        if node.status != StoryDeliveryNodeStatus.LOCKED:
            return f"{node.name} ({node.status.value})", False
    return "Story Ready", False


def render_release_plan_markdown(db: Session, sprint: Sprint, stories: list[Story]) -> str:
    lines = [f"# Release Plan — {sprint.name}", ""]

    lines.append("| Story | Lane Stage | Ready to Release |")
    lines.append("|---|---|---|")
    all_ready = True
    for story in stories:
        stage_label, ready = _lane_stage_label(db, story)
        all_ready = all_ready and ready
        lines.append(f"| {story.title} | {stage_label} | {'Yes' if ready else 'No'} |")

    lines.append("")
    if not stories:
        lines.append("_No stories in this sprint yet — nothing to release._")
    elif all_ready:
        lines.append("**All stories in this sprint have reached Release Ready.**")
    else:
        lines.append("**Not every story in this sprint has reached Release Ready yet — this release is not ready.**")

    return "\n".join(lines)
