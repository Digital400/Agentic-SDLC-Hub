"""Integrations endpoints — foundation only.

Covers: list/get/create/update integration rows, plus connect/disconnect
placeholders. No MCP client exists yet and nothing here ever actually
reaches Jira/Confluence/GitHub/Slack/Teams/Azure DevOps — see
app/models/integration.py's docstring and docs/architecture.md's MCP
integrations section for the intended real architecture. `connect` is
deliberately left unimplemented (501) rather than faking a successful
connection, so the UI's "Connect" button has a real, honest endpoint to
call once wiring a provider is actually in scope.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Integration, IntegrationStatus, User
from app.schemas.integration import IntegrationCreate, IntegrationRead
from app.services.audit import record_audit_log

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _get_integration_or_404(db: Session, integration_id: uuid.UUID) -> Integration:
    integration = db.get(Integration, integration_id)
    if integration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Integration {integration_id} not found")
    return integration


# 1. List integrations --------------------------------------------------------------


@router.get("", response_model=list[IntegrationRead])
def list_integrations(db: Session = Depends(get_db)) -> list[IntegrationRead]:
    integrations = db.query(Integration).order_by(Integration.integration_name).all()
    return [IntegrationRead.from_orm_integration(i) for i in integrations]


# 2. Get integration by id ------------------------------------------------------------


@router.get("/{integration_id}", response_model=IntegrationRead)
def get_integration(integration_id: uuid.UUID, db: Session = Depends(get_db)) -> IntegrationRead:
    return IntegrationRead.from_orm_integration(_get_integration_or_404(db, integration_id))


# 3. Register a (placeholder) integration ----------------------------------------------


@router.post("", response_model=IntegrationRead, status_code=status.HTTP_201_CREATED)
def create_integration(payload: IntegrationCreate, db: Session = Depends(get_db)) -> IntegrationRead:
    """Registers a row for a future integration — does not connect
    anything. Created NOT_CONNECTED, same as every seeded provider row."""
    integration = Integration(
        integration_name=payload.integration_name,
        provider=payload.provider,
        status=IntegrationStatus.NOT_CONNECTED,
        config_json=payload.config_json,
    )
    db.add(integration)
    db.flush()

    record_audit_log(
        db,
        action="integration.created",
        entity_type="Integration",
        entity_id=integration.id,
        extra_data={"provider": integration.provider.value},
    )

    db.commit()
    db.refresh(integration)
    return IntegrationRead.from_orm_integration(integration)


# 4. Connect (placeholder — not implemented) --------------------------------------------


@router.post("/{integration_id}/connect")
def connect_integration(integration_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Deliberately unimplemented — see module docstring. A real
    implementation would start an OAuth/API-key flow, verify it, then set
    status to CONNECTED with a real connected_by. Returns 501 rather than
    faking success so callers can't mistake this for a working connection."""
    _get_integration_or_404(db, integration_id)
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Connecting a real integration isn't implemented yet — this endpoint is a placeholder "
        "for the future MCP-based connection flow (see docs/architecture.md).",
    )


# 5. Disconnect -----------------------------------------------------------------------


@router.post("/{integration_id}/disconnect", response_model=IntegrationRead)
def disconnect_integration(integration_id: uuid.UUID, db: Session = Depends(get_db)) -> IntegrationRead:
    """Resets an integration back to NOT_CONNECTED. Harmless to call even
    though nothing is ever really connected today — kept symmetrical with
    `connect` for when that becomes real."""
    integration = _get_integration_or_404(db, integration_id)
    integration.status = IntegrationStatus.NOT_CONNECTED
    integration.connected_by_id = None
    integration.last_synced_at = None

    record_audit_log(
        db,
        action="integration.disconnected",
        entity_type="Integration",
        entity_id=integration.id,
        extra_data={"provider": integration.provider.value},
    )

    db.commit()
    db.refresh(integration)
    return IntegrationRead.from_orm_integration(integration)
