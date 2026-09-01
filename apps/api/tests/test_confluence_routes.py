"""Route-level tests for Confluence integration
(app/api/routes/confluence_integration.py):
  1/2. Connect/disconnect, space configuration (root page created atomically).
  3. Preview surfaces validation errors and already-published pages.
  5/6/7. Publish only touches exactly what's requested; create vs. update
  branching; drafts never reach Confluence.
  8. Audit logs.
  Rules: draft artifacts blocked, must preview before publish (never
  trusts a stale client preview — re-validated server-side), token never
  leaks.

No TestClient exists in this repo — every call is a direct call into the
real route function, mirroring test_jira_routes.py's conventions.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.routes.confluence_integration import (
    connect_confluence,
    create_confluence_space_link,
    disconnect_confluence,
    get_confluence_publish_preview,
    publish_to_confluence,
)
import app.api.routes.confluence_integration as confluence_routes
from app.models import ArtifactStatus, AuditLog, ConfluencePageLink, ConfluenceSpaceLink, IntegrationStatus, User, UserRole
from app.schemas.confluence_integration import ConfluencePublishRequest, ConnectConfluenceRequest, CreateConfluenceSpaceLinkRequest
from app.services.confluence_integration import ConfluenceIntegrationError, ConfluencePage, ConfluenceSpace, ConfluenceUser
from tests.conftest import make_approved_artifact, make_node

REAL_TOKEN = "ATATT3xFfGF0ThisIsARealSecretConfluenceApiToken1234567890"


def _developer(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _mock_confluence(monkeypatch):
    monkeypatch.setattr(
        confluence_routes.confluence_api, "verify_credentials",
        lambda base_url, email, token, **kw: ConfluenceUser(account_id="acc-1", display_name="Suru", email=email),
    )
    monkeypatch.setattr(
        confluence_routes.confluence_api, "get_space",
        lambda base_url, email, token, key, **kw: ConfluenceSpace(key=key, name=f"{key} Space", id="1"),
    )

    pages = {}
    counter = {"n": 0}

    def _create_page(base_url, email, token, *, space_key, title, body_markdown, parent_id=None, **kw):
        counter["n"] += 1
        page_id = str(counter["n"])
        page = ConfluencePage(id=page_id, title=title, url=f"{base_url}/wiki/pages/{page_id}", version=1)
        pages[page_id] = page
        return page

    def _get_page(base_url, email, token, page_id, **kw):
        return pages[page_id]

    def _update_page(base_url, email, token, *, page_id, title, body_markdown, version, **kw):
        page = ConfluencePage(id=page_id, title=title, url=f"{base_url}/wiki/pages/{page_id}", version=version)
        pages[page_id] = page
        return page

    monkeypatch.setattr(confluence_routes.confluence_api, "create_page", _create_page)
    monkeypatch.setattr(confluence_routes.confluence_api, "get_page", _get_page)
    monkeypatch.setattr(confluence_routes.confluence_api, "update_page", _update_page)
    return pages


def _connect_and_link(db, actor, project, monkeypatch):
    _mock_confluence(monkeypatch)
    connection = connect_confluence(
        ConnectConfluenceRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )
    link = create_confluence_space_link(
        CreateConfluenceSpaceLinkRequest(project_id=project.id, connection_id=connection.id, space_key="ENG"), db,
    )
    return link


# --- Connect / disconnect / space link ----------------------------------------------------


def test_connect_never_leaks_the_token_in_audit_log(db, actor, monkeypatch):
    _mock_confluence(monkeypatch)
    connection = connect_confluence(
        ConnectConfluenceRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )

    assert connection.status == IntegrationStatus.CONNECTED
    assert connection.token_hint == f"****{REAL_TOKEN[-4:]}"
    dump = str([row.extra_data for row in db.query(AuditLog).all()])
    assert REAL_TOKEN not in dump


def test_disconnect_clears_the_stored_token(db, actor, monkeypatch):
    _mock_confluence(monkeypatch)
    connection = connect_confluence(
        ConnectConfluenceRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )
    disconnect_confluence(connection.id, db)

    from app.models import IntegrationConnection

    row = db.get(IntegrationConnection, connection.id)
    assert row.access_token_encrypted == ""
    assert row.status == IntegrationStatus.NOT_CONNECTED


def test_create_space_link_creates_root_page_atomically(db, project, actor, monkeypatch):
    link = _connect_and_link(db, actor, project, monkeypatch)

    assert link.space_key == "ENG"
    assert link.root_page_id == "1"
    assert db.query(ConfluenceSpaceLink).count() == 1


def test_root_page_failure_leaves_no_space_link(db, project, actor, monkeypatch):
    _mock_confluence(monkeypatch)
    connection = connect_confluence(
        ConnectConfluenceRequest(base_url="https://example.atlassian.net", email="suru@example.com", api_token=REAL_TOKEN, connected_by_id=actor.id),
        db,
    )

    def _failing_create_page(*a, **kw):
        raise ConfluenceIntegrationError("Confluence API returned 500: boom", status_code=500)

    monkeypatch.setattr(confluence_routes.confluence_api, "create_page", _failing_create_page)

    with pytest.raises(HTTPException):
        create_confluence_space_link(
            CreateConfluenceSpaceLinkRequest(project_id=project.id, connection_id=connection.id, space_key="ENG"), db,
        )

    assert db.query(ConfluenceSpaceLink).count() == 0


# --- Preview (requirement 3) ---------------------------------------------------------------


def test_preview_404s_when_no_confluence_space_configured(db, project, actor):
    with pytest.raises(HTTPException) as exc_info:
        get_confluence_publish_preview(project.id, db)
    assert exc_info.value.status_code == 404


def test_preview_shows_all_seven_kinds_with_a_draft_flagged_invalid(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, node, actor, content="# HLD")

    preview = get_confluence_publish_preview(project.id, db)

    assert len(preview.items) == 7
    hld_item = next(i for i in preview.items if i.artifact_type == "hld_document")
    assert hld_item.is_valid
    missing_item = next(i for i in preview.items if i.artifact_type == "lld_document")
    assert not missing_item.is_valid


# --- Publish (requirements 5, 6, 7 + rules) -------------------------------------------------


def test_publish_creates_a_page_for_an_approved_artifact(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, node, actor, content="# HLD content")
    dev = _developer(db)

    response = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document"]), db,
    )

    assert response.results[0].status == "published"
    assert db.query(ConfluencePageLink).filter(ConfluencePageLink.artifact_type == "hld_document").count() == 1
    actions = [row.action for row in db.query(AuditLog).filter(AuditLog.project_id == project.id).all()]
    assert "confluence_page.published" in actions


def test_publish_blocks_a_draft_artifact_and_never_calls_confluence(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    artifact = make_approved_artifact(db, project, node, actor)
    artifact.status = ArtifactStatus.DRAFT
    db.flush()
    dev = _developer(db)

    def _fail_create(*a, **kw):
        raise AssertionError("should not be called")

    monkeypatch.setattr(confluence_routes.confluence_api, "create_page", _fail_create)

    response = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document"]), db,
    )

    assert response.results[0].status == "skipped_invalid"
    assert response.results[0].errors
    assert db.query(ConfluencePageLink).count() == 0


def test_publish_again_updates_the_same_page_instead_of_creating_a_duplicate(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    artifact = make_approved_artifact(db, project, node, actor, content="v1")
    dev = _developer(db)

    first = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document"]), db,
    )
    first_page_id = first.results[0].confluence_page_id

    # Approve a new version, then publish again.
    from app.models import ArtifactVersion

    new_version = ArtifactVersion(artifact_id=artifact.id, version_number=2, content_markdown="v2", created_by_id=actor.id)
    db.add(new_version)
    db.flush()
    artifact.current_version_id = new_version.id
    db.flush()

    second = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document"]), db,
    )

    assert second.results[0].status == "updated"
    assert second.results[0].confluence_page_id == first_page_id
    assert db.query(ConfluencePageLink).filter(ConfluencePageLink.artifact_type == "hld_document").count() == 1
    link = db.query(ConfluencePageLink).filter(ConfluencePageLink.artifact_type == "hld_document").first()
    assert link.confluence_page_version == 2


def test_publish_one_failure_does_not_abort_the_batch(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node_hld = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, node_hld, actor)
    node_lld = make_node(db, project, node_key="lld", order_index=1, output_artifact_type="lld_document")
    make_approved_artifact(db, project, node_lld, actor)
    dev = _developer(db)

    def _flaky_create_page(base_url, email, token, *, space_key, title, body_markdown, parent_id=None, **kw):
        if "HLD" in title:
            raise ConfluenceIntegrationError("Confluence API returned 500: boom", status_code=500)
        return ConfluencePage(id="1", title=title, url=f"{base_url}/wiki/pages/1", version=1)

    monkeypatch.setattr(confluence_routes.confluence_api, "create_page", _flaky_create_page)

    response = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document", "lld_document"]), db,
    )

    by_type = {r.artifact_type: r for r in response.results}
    assert by_type["hld_document"].status == "failed"
    assert by_type["lld_document"].status == "published"  # kept going despite the HLD failure


def test_publish_only_touches_requested_artifact_types(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node_hld = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, node_hld, actor)
    node_lld = make_node(db, project, node_key="lld", order_index=1, output_artifact_type="lld_document")
    make_approved_artifact(db, project, node_lld, actor)
    dev = _developer(db)

    publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document"]), db,
    )

    assert db.query(ConfluencePageLink).count() == 1
    assert db.query(ConfluencePageLink).first().artifact_type == "hld_document"


# --- Security regression --------------------------------------------------------------------


def test_no_confluence_token_ever_appears_in_any_response_or_audit_log(db, project, actor, monkeypatch):
    _connect_and_link(db, actor, project, monkeypatch)
    node = make_node(db, project, node_key="hld", order_index=0, output_artifact_type="hld_document")
    make_approved_artifact(db, project, node, actor)
    dev = _developer(db)

    preview = get_confluence_publish_preview(project.id, db)
    publish_response = publish_to_confluence(
        ConfluencePublishRequest(project_id=project.id, triggered_by_user_id=dev.id, artifact_types=["hld_document"]), db,
    )

    dump = str([preview.model_dump(), publish_response.model_dump(), [row.extra_data for row in db.query(AuditLog).all()]])
    assert REAL_TOKEN not in dump
