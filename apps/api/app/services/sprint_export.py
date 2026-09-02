"""Deterministic Markdown renderer for a Sprint's plan document — the
Sprint Planning stage's "draft" (see workflows/scrum-story-lanes-workflow.json).
Sprint Planning is a lightweight grouping + gating stage, not an AI agent
(see the plan's Context section) — a human assigns stories into a Sprint
via POST /sprints, and this just renders what already exists, mirroring
app/services/story_export.py's render_markdown pattern.
"""

from app.models import Sprint, Story


def render_sprint_plan_markdown(sprint: Sprint, stories: list[Story]) -> str:
    lines = [f"# Sprint Plan — {sprint.name}", "", f"**Status:** {sprint.status.value}", ""]

    lines.append("## Stories")
    lines.append("")
    if not stories:
        lines.append("_No stories assigned to this sprint yet._")
    else:
        lines.append("| Story | Priority | Owner | Status |")
        lines.append("|---|---|---|---|")
        for story in stories:
            owner = story.owner.full_name if story.owner is not None else "_unassigned_"
            lines.append(f"| {story.title} | {story.priority or '—'} | {owner} | {story.status.value} |")
    lines.append("")

    lines.append("## Story Owners")
    lines.append("")
    unassigned = [s.title for s in stories if s.owner_user_id is None]
    if unassigned:
        lines.append("**Unassigned:** " + ", ".join(unassigned))
    else:
        lines.append("Every story in this sprint has an owner assigned.")

    return "\n".join(lines)
