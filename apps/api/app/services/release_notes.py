"""Generates a Release's release notes (requirement 6) — deterministic
Markdown assembled from each selected story's own real records, not a
model call: a story summary, its PR summary (from the latest ACCEPTED
ImplementationRun's own drafted `pr_description`), its latest test report
(the story_test_report StoryArtifact — see app/services/testing_agent.py),
and known risks (ImplementationRun.risks + the test report's own bugs
found). Mirrors app/services/release_export.py/sprint_export.py's own
"real renderer, not an AI draft" convention for project-management
documents.
"""

from sqlalchemy.orm import Session

from app.models import (
    ImplementationRun,
    ImplementationRunReviewStatus,
    ImplementationTask,
    Release,
    Story,
    StoryArtifact,
)
from app.services.testing_agent import STORY_TEST_REPORT_ARTIFACT_TYPE


def _latest_accepted_run(db: Session, story: Story) -> ImplementationRun | None:
    task = db.query(ImplementationTask).filter(ImplementationTask.story_id == story.id).first()
    if task is None:
        return None
    return (
        db.query(ImplementationRun)
        .filter(ImplementationRun.implementation_task_id == task.id, ImplementationRun.review_status == ImplementationRunReviewStatus.ACCEPTED)
        .order_by(ImplementationRun.completed_at.desc())
        .first()
    )


def _latest_test_report(db: Session, story: Story) -> StoryArtifact | None:
    return (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_TEST_REPORT_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )


def render_release_notes(db: Session, *, release: Release, stories: list[Story]) -> str:
    lines = [f"# Release Notes — {release.name} (v{release.version})", ""]
    lines.append(f"**Target date:** {release.target_date.isoformat() if release.target_date else 'TBD'}")
    lines.append("")

    if not stories:
        lines.append("_No stories selected for this release yet._")
        return "\n".join(lines)

    lines.append("## Story Summaries")
    for story in stories:
        lines.append(f"### {story.title}")
        lines.append(story.user_story or "(no user story text)")
        if story.business_value:
            lines.append(f"**Business value:** {story.business_value}")
        lines.append("")

    lines.append("## Pull Request Summaries")
    known_risks: list[str] = []
    for story in stories:
        run = _latest_accepted_run(db, story)
        lines.append(f"### {story.title}")
        if run is None:
            lines.append("_No accepted implementation run for this story._")
        else:
            lines.append(run.pr_description.strip() or run.explanation.strip() or "(no PR description recorded)")
            known_risks += [f"{story.title}: {r}" for r in run.risks]
        lines.append("")

    lines.append("## Test Reports")
    for story in stories:
        report = _latest_test_report(db, story)
        lines.append(f"### {story.title}")
        if report is None:
            lines.append("_No test report generated for this story yet._")
        else:
            lines.append(report.content_markdown.strip())
        lines.append("")

    lines.append("## Known Risks")
    if known_risks:
        lines += [f"- {r}" for r in known_risks]
    else:
        lines.append("None recorded.")

    return "\n".join(lines)
