"""Publishes one story's own StoryArtifact (currently just its Story LLD)
to Confluence — the per-story sibling of
app/services/confluence_publish_preview.py's project-level flow. Reuses
the exact same ConfluenceIntegrationService client, ConfluencePageLink
model (story-scoped rows — see that model's own docstring), and
create-or-update-in-place identity rule; only the source of the content
(a StoryArtifact, not a project-level Artifact/ArtifactVersion) differs.

SCOPE: only Story LLD is publishable today (STORY_CONFLUENCE_ARTIFACT_TYPES
has exactly one entry) — Implementation Plan/Test Scenarios have no
stated requirement to publish externally; extending this dict is the only
change needed if that changes later, same as
CONFLUENCE_ARTIFACT_TYPES' own fixed-set shape.

GATE: a story's LLD can only be published once its LLD_REVIEW node has
completed (i.e. a Tech Lead approved it) — same "drafts cannot be
published as official pages" rule the project-level flow enforces via
ArtifactStatus.APPROVED, expressed here against the lane's own review
gate since StoryArtifact carries no approval status field of its own.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import ConfluencePageLink, ConfluenceSpaceLink, Story, StoryArtifact, StoryDeliveryNodeStatus
from app.services.story_lld_agent import STORY_LLD_ARTIFACT_TYPE

STORY_CONFLUENCE_ARTIFACT_TYPES: dict[str, str] = {
    STORY_LLD_ARTIFACT_TYPE: "Story LLD",
}


@dataclass
class StoryConfluencePublishItem:
    artifact_type: str
    label: str
    story_artifact_id: str | None
    content_preview: str
    validation_errors: list[str] = field(default_factory=list)
    already_published: ConfluencePageLink | None = None
    update_available: bool = False

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


@dataclass
class StoryConfluencePublishPreview:
    items: list[StoryConfluencePublishItem] = field(default_factory=list)


def _existing_links(db: Session, *, space_link_id, story_id) -> dict[str, ConfluencePageLink]:
    rows = (
        db.query(ConfluencePageLink)
        .filter(ConfluencePageLink.confluence_space_link_id == space_link_id, ConfluencePageLink.story_id == story_id)
        .all()
    )
    return {row.artifact_type: row for row in rows}


def build_story_confluence_publish_preview(
    db: Session, *, story: Story, space_link: ConfluenceSpaceLink
) -> StoryConfluencePublishPreview:
    links = _existing_links(db, space_link_id=space_link.id, story_id=story.id)
    lane = story.delivery_lane
    lld_review_completed = False
    if lane is not None:
        lld_review = next((n for n in lane.nodes if n.node_key == "LLD_REVIEW"), None)
        lld_review_completed = lld_review is not None and lld_review.status == StoryDeliveryNodeStatus.COMPLETED

    items: list[StoryConfluencePublishItem] = []
    for artifact_type, label in STORY_CONFLUENCE_ARTIFACT_TYPES.items():
        story_artifact = (
            db.query(StoryArtifact)
            .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == artifact_type)
            .order_by(StoryArtifact.version_number.desc())
            .first()
        )
        existing_link = links.get(artifact_type)

        errors: list[str] = []
        content_preview = ""
        story_artifact_id = None
        update_available = False

        if story_artifact is None:
            errors.append(f"No {label} has been drafted for this story yet.")
        elif not lld_review_completed:
            # Rule: draft artifacts cannot be published as official pages
            # — the story-scoped equivalent of ArtifactStatus.APPROVED.
            errors.append(f"{label} is not approved yet — approve it (LLD Review) before publishing.")
        else:
            story_artifact_id = str(story_artifact.id)
            content_preview = story_artifact.content_markdown
            if existing_link is not None and existing_link.story_artifact_id != story_artifact.id:
                update_available = True

        items.append(
            StoryConfluencePublishItem(
                artifact_type=artifact_type, label=label, story_artifact_id=story_artifact_id,
                content_preview=content_preview, validation_errors=errors,
                already_published=existing_link, update_available=update_available,
            )
        )

    return StoryConfluencePublishPreview(items=items)
