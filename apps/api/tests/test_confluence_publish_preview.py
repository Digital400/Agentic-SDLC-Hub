"""Tests for app/services/confluence_publish_preview.py — the fixed
seven-artifact-kind preview with validation errors (rule: draft artifacts
cannot be published) and update-availability detection (requirement 7).
"""

import uuid

from app.models import (
    ArtifactStatus,
    ArtifactVersion,
    ConfluencePageLink,
    ConfluenceSpaceLink,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
)
from app.services.confluence_publish_preview import build_confluence_publish_preview
from tests.conftest import make_approved_artifact, make_node


def _confluence_space_link(db, project) -> ConfluenceSpaceLink:
    integration = Integration(integration_name="Confluence", provider=IntegrationProvider.CONFLUENCE, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="not-a-real-fernet-token", token_last_four="7890",
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    link = ConfluenceSpaceLink(
        project=project, connection=connection, space_key="ENG", space_name="Engineering",
        root_page_id="1", root_page_url="https://example.atlassian.net/wiki/spaces/ENG/pages/1",
    )
    db.add(link)
    db.flush()
    return link


def test_missing_artifact_produces_a_validation_error(db, project, actor):
    link = _confluence_space_link(db, project)
    preview = build_confluence_publish_preview(db, project=project, space_link=link)

    hld_item = next(i for i in preview.items if i.artifact_type == "hld_document")
    assert not hld_item.is_valid
    assert "no hld artifact exists yet" in hld_item.validation_errors[0].lower()


def test_draft_artifact_is_flagged_invalid(db, project, actor):
    link = _confluence_space_link(db, project)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    artifact = make_approved_artifact(db, project, node, actor)
    artifact.status = ArtifactStatus.DRAFT
    db.flush()

    preview = build_confluence_publish_preview(db, project=project, space_link=link)
    hld_item = next(i for i in preview.items if i.artifact_type == "hld_document")

    assert not hld_item.is_valid
    assert "draft" in hld_item.validation_errors[0].lower()


def test_approved_not_yet_published_item_is_valid_with_no_page(db, project, actor):
    link = _confluence_space_link(db, project)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, node, actor, content="# HLD\ncontent")

    preview = build_confluence_publish_preview(db, project=project, space_link=link)
    hld_item = next(i for i in preview.items if i.artifact_type == "hld_document")

    assert hld_item.is_valid
    assert hld_item.content_preview == "# HLD\ncontent"
    assert hld_item.already_published is None
    assert not hld_item.update_available


def test_already_published_current_version_shows_no_update_available(db, project, actor):
    link = _confluence_space_link(db, project)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    artifact = make_approved_artifact(db, project, node, actor)
    page_link = ConfluencePageLink(
        project_id=project.id, confluence_space_link_id=link.id, artifact_type="hld_document",
        artifact_id=artifact.id, artifact_version_id=artifact.current_version_id,
        confluence_page_id="555", confluence_page_url="https://x/555", confluence_page_title="HLD",
        confluence_page_version=1,
    )
    db.add(page_link)
    db.flush()

    preview = build_confluence_publish_preview(db, project=project, space_link=link)
    hld_item = next(i for i in preview.items if i.artifact_type == "hld_document")

    assert hld_item.already_published is not None
    assert not hld_item.update_available


def test_new_approved_version_after_publish_marks_update_available(db, project, actor):
    link = _confluence_space_link(db, project)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    artifact = make_approved_artifact(db, project, node, actor)
    page_link = ConfluencePageLink(
        project_id=project.id, confluence_space_link_id=link.id, artifact_type="hld_document",
        artifact_id=artifact.id, artifact_version_id=artifact.current_version_id,
        confluence_page_id="555", confluence_page_url="https://x/555", confluence_page_title="HLD",
        confluence_page_version=1,
    )
    db.add(page_link)
    db.flush()

    # A new version is drafted and approved after the first publish.
    new_version = ArtifactVersion(artifact_id=artifact.id, version_number=2, content_markdown="v2 content", created_by_id=actor.id)
    db.add(new_version)
    db.flush()
    artifact.current_version_id = new_version.id
    db.flush()

    preview = build_confluence_publish_preview(db, project=project, space_link=link)
    hld_item = next(i for i in preview.items if i.artifact_type == "hld_document")

    assert hld_item.update_available


def test_release_notes_maps_to_deployment_record_with_disclosed_label(db, project, actor):
    link = _confluence_space_link(db, project)
    preview = build_confluence_publish_preview(db, project=project, space_link=link)

    release_item = next(i for i in preview.items if i.artifact_type == "deployment_record")
    assert "release notes" in release_item.label.lower()
    assert "deployment record" in release_item.label.lower()
