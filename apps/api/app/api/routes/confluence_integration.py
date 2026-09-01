"""Confluence integration endpoints.

Covers: connect/disconnect a Confluence API token, configure a project's
Confluence space (which eagerly creates the project's page-hierarchy root
page), preview what would be published (the seven fixed artifact kinds,
each with validation errors and update-availability), and publish an
explicitly human-selected subset (create or update, per artifact type).

HARD RULE (see app/services/confluence_integration.py's module docstring):
no delete, move, or permissions-change call exists anywhere in the
Confluence client this router uses — only create_page/update_page
(writes) and three reads. `/confluence/publish` never touches anything
beyond exactly what's named in `artifact_types` — there is no "publish
everything" endpoint.

RULES enforced here (not just by the preview):
  - Draft artifacts cannot be published — every artifact_type is
    re-validated against the live Artifact/ArtifactStatus at publish
    time, never trusting a stale client-held preview.
  - User must preview before publishing — nothing in this router creates
    or updates a ConfluencePageLink except in direct response to
    /confluence/publish, and /confluence/publish itself only ever acts on
    the exact list of artifact_types the caller passes.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret, last_four
from app.models import (
    Artifact,
    ArtifactStatus,
    ConfluencePageLink,
    ConfluenceSpaceLink,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Project,
    User,
)
from app.schemas.confluence_integration import (
    ConfluenceConnectionRead,
    ConfluencePageLinkRead,
    ConfluencePublishItemRead,
    ConfluencePublishPreviewRead,
    ConfluencePublishRequest,
    ConfluencePublishResponse,
    ConfluencePublishResultItem,
    ConfluenceSpaceLinkRead,
    ConnectConfluenceRequest,
    CreateConfluenceSpaceLinkRequest,
)
from app.services import confluence_integration as confluence_api
from app.services.audit import record_audit_log
from app.services.confluence_integration import ConfluenceIntegrationError
from app.services.confluence_publish_preview import (
    CONFLUENCE_ARTIFACT_TYPES,
    ConfluencePublishItem,
    build_confluence_publish_preview,
)

router = APIRouter(prefix="/confluence", tags=["confluence"])


def _get_connection_or_404(db: Session, connection_id: uuid.UUID) -> IntegrationConnection:
    connection = db.get(IntegrationConnection, connection_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Confluence connection {connection_id} not found")
    return connection


def _get_project_or_404(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} not found")
    return project


def _get_space_link_or_404(db: Session, project_id: uuid.UUID) -> ConfluenceSpaceLink:
    link = db.query(ConfluenceSpaceLink).filter(ConfluenceSpaceLink.project_id == project_id).first()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Project {project_id} has no configured Confluence space yet.")
    return link


def decrypt_confluence_token(connection: IntegrationConnection) -> str:
    """Decrypts the token exactly once, for exactly one outbound
    Confluence call — never cached, never logged. Mirrors
    app/api/routes/jira_integration.py's decrypt_jira_token."""
    try:
        return decrypt_secret(connection.access_token_encrypted)
    except SecretDecryptionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This Confluence connection's stored credential can no longer be decrypted — reconnect Confluence.",
        ) from exc


def _confluence_error_to_http(exc: ConfluenceIntegrationError) -> HTTPException:
    if exc.status_code in (401, 403):
        return HTTPException(status.HTTP_409_CONFLICT, f"Confluence rejected the stored credential: {exc}")
    if exc.status_code == 404:
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


def _config(integration: Integration) -> dict:
    return integration.config_json or {}


# 1. Connect a Confluence API token -----------------------------------------------------


@router.post("/connections", response_model=ConfluenceConnectionRead, status_code=status.HTTP_201_CREATED)
def connect_confluence(payload: ConnectConfluenceRequest, db: Session = Depends(get_db)) -> ConfluenceConnectionRead:
    connected_by = db.get(User, payload.connected_by_id)
    if connected_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"connected_by_id {payload.connected_by_id} does not match an existing user")

    try:
        confluence_api.verify_credentials(payload.base_url, payload.email, payload.api_token)
    except ConfluenceIntegrationError as exc:
        raise _confluence_error_to_http(exc) from exc

    integration = db.query(Integration).filter(Integration.provider == IntegrationProvider.CONFLUENCE).first()
    if integration is None:
        integration = Integration(
            integration_name="Confluence", provider=IntegrationProvider.CONFLUENCE, status=IntegrationStatus.NOT_CONNECTED
        )
        db.add(integration)
        db.flush()

    integration.config_json = {"base_url": payload.base_url, "email": payload.email}
    integration.status = IntegrationStatus.CONNECTED
    integration.connected_by_id = connected_by.id

    connection = IntegrationConnection(
        integration=integration,
        access_token_encrypted=encrypt_secret(payload.api_token),
        token_last_four=last_four(payload.api_token),
        connected_by=connected_by,
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()

    # SECURITY: extra_data below must never include payload.api_token —
    # only the non-secret facts a real audit trail needs.
    record_audit_log(
        db, actor_user_id=connected_by.id, action="confluence.connected", entity_type="IntegrationConnection",
        entity_id=connection.id, extra_data={"email": payload.email, "base_url": payload.base_url},
    )

    db.commit()
    db.refresh(connection)
    return ConfluenceConnectionRead.from_orm_connection(connection, base_url=payload.base_url, email=payload.email)


@router.get("/connections", response_model=list[ConfluenceConnectionRead])
def list_confluence_connections(db: Session = Depends(get_db)) -> list[ConfluenceConnectionRead]:
    connections = (
        db.query(IntegrationConnection)
        .join(Integration, IntegrationConnection.integration_id == Integration.id)
        .filter(Integration.provider == IntegrationProvider.CONFLUENCE)
        .order_by(IntegrationConnection.created_at.desc())
        .all()
    )
    return [ConfluenceConnectionRead.from_orm_connection(c, **_config(c.integration)) for c in connections if _config(c.integration)]


@router.post("/connections/{connection_id}/disconnect", response_model=ConfluenceConnectionRead)
def disconnect_confluence(connection_id: uuid.UUID, db: Session = Depends(get_db)) -> ConfluenceConnectionRead:
    connection = _get_connection_or_404(db, connection_id)
    connection.status = IntegrationStatus.NOT_CONNECTED
    connection.integration.status = IntegrationStatus.NOT_CONNECTED
    # Security hygiene: same as GitHub/Jira's disconnect — clear the live,
    # decryptable credential rather than just flipping status.
    connection.access_token_encrypted = ""
    connection.token_last_four = ""
    config = _config(connection.integration)

    record_audit_log(
        db, action="confluence.disconnected", entity_type="IntegrationConnection", entity_id=connection.id,
        extra_data={"email": config.get("email")},
    )

    db.commit()
    db.refresh(connection)
    return ConfluenceConnectionRead.from_orm_connection(connection, base_url=config.get("base_url", ""), email=config.get("email", ""))


# 2. Configure a project's Confluence space (creates the hierarchy root page) -----------


@router.post("/spaces", response_model=ConfluenceSpaceLinkRead, status_code=status.HTTP_201_CREATED)
def create_confluence_space_link(payload: CreateConfluenceSpaceLinkRequest, db: Session = Depends(get_db)) -> ConfluenceSpaceLink:
    project = _get_project_or_404(db, payload.project_id)
    connection = _get_connection_or_404(db, payload.connection_id)
    if connection.status != IntegrationStatus.CONNECTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This Confluence connection isn't CONNECTED.")

    config = _config(connection.integration)
    token = decrypt_confluence_token(connection)
    base_url = config.get("base_url", "")
    email = config.get("email", "")

    try:
        space = confluence_api.get_space(base_url, email, token, payload.space_key)
        # Requirement 5 — one root page per project, created eagerly and
        # atomically with this link: if this call fails, nothing below
        # runs and no ConfluenceSpaceLink is saved (no half-configured
        # state — see the model's class docstring).
        root_page = confluence_api.create_page(
            base_url, email, token, space_key=space.key, title=project.name,
            body_markdown=f"Root page for **{project.name}** — published artifacts from Agentic SDLC Hub appear as child pages here.",
        )
    except ConfluenceIntegrationError as exc:
        raise _confluence_error_to_http(exc) from exc

    link = ConfluenceSpaceLink(
        project=project, connection=connection, space_key=space.key, space_name=space.name,
        root_page_id=root_page.id, root_page_url=root_page.url,
    )
    db.add(link)
    db.flush()

    record_audit_log(
        db, project_id=project.id, action="confluence_space.connected", entity_type="ConfluenceSpaceLink", entity_id=link.id,
        extra_data={"space_key": space.key, "space_name": space.name, "root_page_id": root_page.id},
    )

    db.commit()
    db.refresh(link)
    return link


# 3. Preview (requirement 3 — must preview before publishing) ---------------------------


def _to_item_read(item: ConfluencePublishItem) -> ConfluencePublishItemRead:
    return ConfluencePublishItemRead(
        artifact_type=item.artifact_type, label=item.label, artifact_id=item.artifact_id,
        artifact_status=item.artifact_status, content_preview=item.content_preview,
        validation_errors=item.validation_errors,
        already_published=ConfluencePageLinkRead.model_validate(item.already_published) if item.already_published else None,
        update_available=item.update_available,
    )


@router.get("/projects/{project_id}/publish-preview", response_model=ConfluencePublishPreviewRead)
def get_confluence_publish_preview(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ConfluencePublishPreviewRead:
    project = _get_project_or_404(db, project_id)
    space_link = _get_space_link_or_404(db, project_id)

    preview = build_confluence_publish_preview(db, project=project, space_link=space_link)
    return ConfluencePublishPreviewRead(
        project_id=project_id, space_key=space_link.space_key, items=[_to_item_read(i) for i in preview.items],
    )


# 4. Publish (requirements 5, 6, 7 — rules: no auto-create, drafts blocked, update-in-place) --


@router.post("/publish", response_model=ConfluencePublishResponse)
def publish_to_confluence(payload: ConfluencePublishRequest, db: Session = Depends(get_db)) -> ConfluencePublishResponse:
    project = _get_project_or_404(db, payload.project_id)
    space_link = _get_space_link_or_404(db, project.id)
    triggered_by = db.get(User, payload.triggered_by_user_id)
    if triggered_by is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"triggered_by_user_id {payload.triggered_by_user_id} does not match an existing user")

    connection = space_link.connection
    config = _config(connection.integration)
    token = decrypt_confluence_token(connection)  # a real write cannot silently degrade to "no token"
    base_url = config.get("base_url", "")
    email = config.get("email", "")

    # Re-validate against the live DB — never trust a stale client preview.
    preview = build_confluence_publish_preview(db, project=project, space_link=space_link)
    items_by_type = {i.artifact_type: i for i in preview.items}

    results: list[ConfluencePublishResultItem] = []

    for artifact_type in payload.artifact_types:
        item = items_by_type.get(artifact_type)
        if item is None:
            results.append(ConfluencePublishResultItem(
                artifact_type=artifact_type, status="skipped_invalid",
                errors=[f"\"{artifact_type}\" is not a publishable Confluence artifact type."] if artifact_type not in CONFLUENCE_ARTIFACT_TYPES else ["Item not found in the current preview."],
            ))
            continue
        if not item.is_valid:
            # Rule: draft artifacts cannot be published as official pages.
            results.append(ConfluencePublishResultItem(artifact_type=artifact_type, status="skipped_invalid", errors=item.validation_errors))
            continue

        artifact = db.get(Artifact, uuid.UUID(item.artifact_id))
        title = f"{project.name} — {item.label}"

        try:
            if item.already_published is not None:
                live_page = confluence_api.get_page(base_url, email, token, item.already_published.confluence_page_id)
                page = confluence_api.update_page(
                    base_url, email, token, page_id=live_page.id, title=title,
                    body_markdown=item.content_preview, version=live_page.version + 1,
                )
                link = item.already_published
                link.artifact_version_id = artifact.current_version_id
                link.confluence_page_version = page.version
                link.triggered_by_user_id = triggered_by.id
                db.flush()
                record_audit_log(
                    db, project_id=project.id, actor_user_id=triggered_by.id, action="confluence_page.updated",
                    entity_type="ConfluencePageLink", entity_id=link.id,
                    extra_data={"artifact_type": artifact_type, "confluence_page_id": page.id, "confluence_page_version": page.version},
                )
                results.append(ConfluencePublishResultItem(artifact_type=artifact_type, status="updated", confluence_page_id=page.id, confluence_page_url=page.url))
            else:
                page = confluence_api.create_page(
                    base_url, email, token, space_key=space_link.space_key, title=title,
                    body_markdown=item.content_preview, parent_id=space_link.root_page_id,
                )
                link = ConfluencePageLink(
                    project_id=project.id, confluence_space_link_id=space_link.id, artifact_type=artifact_type,
                    artifact_id=artifact.id, artifact_version_id=artifact.current_version_id,
                    confluence_page_id=page.id, confluence_page_url=page.url, confluence_page_title=title,
                    confluence_page_version=page.version, triggered_by_user_id=triggered_by.id,
                )
                db.add(link)
                db.flush()
                record_audit_log(
                    db, project_id=project.id, actor_user_id=triggered_by.id, action="confluence_page.published",
                    entity_type="ConfluencePageLink", entity_id=link.id,
                    extra_data={"artifact_type": artifact_type, "confluence_page_id": page.id, "confluence_page_version": page.version},
                )
                results.append(ConfluencePublishResultItem(artifact_type=artifact_type, status="published", confluence_page_id=page.id, confluence_page_url=page.url))
        except ConfluenceIntegrationError as exc:
            # One item's failure doesn't abort the batch.
            results.append(ConfluencePublishResultItem(artifact_type=artifact_type, status="failed", errors=[str(exc)]))
            continue

    db.commit()
    return ConfluencePublishResponse(results=results)
