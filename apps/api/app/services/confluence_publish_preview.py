"""Builds the full "what would be published to Confluence" preview — the
seven fixed artifact kinds this integration supports, each with validation
errors and whether it's already linked to a real Confluence page.

CONFLUENCE_ARTIFACT_TYPES is the disclosed mapping from this codebase's
own artifact_type strings to the requested Confluence content:

    Requirement Summary  -> intake_summary
    Problem Discovery    -> problem_statement
    Solution Discovery   -> solution_options_doc
    HLD                  -> hld_document
    LLD                  -> lld_document
    Test Report          -> test_report
    Release Notes        -> deployment_record   (see the label below — no
                             dedicated release-notes artifact type exists
                             anywhere in this codebase; the `release`
                             workflow stage's real output is a deployment
                             record, published here under an explicitly
                             labeled substitution rather than silently
                             mislabeling it or inventing a new type.)

Read-only — never creates or updates anything. See
app/models/confluence_page_link.py's class docstring for the identity/
duplicate-prevention rule and app/api/routes/confluence_integration.py's
/confluence/publish for where a row is actually written.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Artifact, ArtifactStatus, ConfluencePageLink, ConfluenceSpaceLink, Project

CONFLUENCE_ARTIFACT_TYPES: dict[str, str] = {
    "intake_summary": "Requirement Summary",
    "problem_statement": "Problem Discovery",
    "solution_options_doc": "Solution Discovery",
    "hld_document": "HLD",
    "lld_document": "LLD",
    "test_report": "Test Report",
    "deployment_record": "Release Notes (from the deployment record)",
}


@dataclass
class ConfluencePublishItem:
    artifact_type: str
    label: str
    artifact_id: str | None
    artifact_status: str | None  # None if no artifact exists yet
    content_preview: str  # full content_markdown — "must preview before publishing" rule
    validation_errors: list[str] = field(default_factory=list)
    already_published: ConfluencePageLink | None = None
    update_available: bool = False  # already published, but the current APPROVED version differs

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


@dataclass
class ConfluencePublishPreview:
    items: list[ConfluencePublishItem] = field(default_factory=list)


def _existing_links(db: Session, space_link_id) -> dict[str, ConfluencePageLink]:
    rows = db.query(ConfluencePageLink).filter(ConfluencePageLink.confluence_space_link_id == space_link_id).all()
    return {row.artifact_type: row for row in rows}


def build_confluence_publish_preview(
    db: Session, *, project: Project, space_link: ConfluenceSpaceLink
) -> ConfluencePublishPreview:
    links = _existing_links(db, space_link.id)
    items: list[ConfluencePublishItem] = []

    for artifact_type, label in CONFLUENCE_ARTIFACT_TYPES.items():
        artifact = (
            db.query(Artifact)
            .filter(Artifact.project_id == project.id, Artifact.artifact_type == artifact_type)
            .order_by(Artifact.created_at.desc())
            .first()
        )
        existing_link = links.get(artifact_type)

        errors: list[str] = []
        content_preview = ""
        artifact_id = None
        artifact_status = None
        update_available = False

        if artifact is None:
            errors.append(f"No {label} artifact exists yet.")
        else:
            artifact_id = str(artifact.id)
            artifact_status = artifact.status.value
            if artifact.status != ArtifactStatus.APPROVED:
                # Rule: draft artifacts cannot be published as official pages.
                errors.append(f"Latest {label} is {artifact.status.value} — only APPROVED artifacts can be published.")
            elif artifact.current_version is None:
                errors.append(f"{label} has no version to publish.")
            else:
                content_preview = artifact.current_version.content_markdown
                if existing_link is not None and existing_link.artifact_version_id != artifact.current_version_id:
                    update_available = True

        items.append(
            ConfluencePublishItem(
                artifact_type=artifact_type,
                label=label,
                artifact_id=artifact_id,
                artifact_status=artifact_status,
                content_preview=content_preview,
                validation_errors=errors,
                already_published=existing_link,
                update_available=update_available,
            )
        )

    return ConfluencePublishPreview(items=items)
