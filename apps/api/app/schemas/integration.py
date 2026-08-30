import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IntegrationProvider, IntegrationStatus
from app.schemas.validators import NonBlankStr


class IntegrationCreate(BaseModel):
    """Registers a placeholder row for a future integration — does not
    connect anything. See app/models/integration.py's docstring."""

    integration_name: NonBlankStr = Field(..., max_length=255)
    provider: IntegrationProvider
    config_json: dict[str, Any] | None = None


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_name: str
    provider: IntegrationProvider
    status: IntegrationStatus
    config_json: dict[str, Any] | None
    connected_by_id: uuid.UUID | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime

    # Denormalized for the Integrations settings UI.
    connected_by_name: str | None

    @classmethod
    def from_orm_integration(cls, integration) -> "IntegrationRead":
        return cls(
            id=integration.id,
            integration_name=integration.integration_name,
            provider=integration.provider,
            status=integration.status,
            config_json=integration.config_json,
            connected_by_id=integration.connected_by_id,
            last_synced_at=integration.last_synced_at,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
            connected_by_name=integration.connected_by.full_name if integration.connected_by else None,
        )
