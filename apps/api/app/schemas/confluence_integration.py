import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IntegrationStatus
from app.schemas.validators import NonBlankStr


class ConnectConfluenceRequest(BaseModel):
    """Body for POST /confluence/connections. `api_token` is write-only —
    it is verified against Confluence, encrypted, and stored; it is never
    echoed back in this or any other response (see ConfluenceConnectionRead,
    which deliberately has no token field). `base_url`/`email` are
    non-secret and stored plainly in Integration.config_json."""

    base_url: NonBlankStr = Field(..., description="e.g. https://yourcompany.atlassian.net")
    email: NonBlankStr = Field(..., description="The Atlassian account email the API token belongs to.")
    api_token: NonBlankStr = Field(..., description="A Confluence Cloud API token (id.atlassian.com -> Security -> API tokens).")
    connected_by_id: uuid.UUID = Field(..., description="Existing user id.")


class ConfluenceConnectionRead(BaseModel):
    """Deliberately has NO token field, encrypted or otherwise. `token_hint`
    (e.g. "****d3f9") is the only thing a UI can show about the stored
    credential."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_id: uuid.UUID
    base_url: str
    email: str
    status: IntegrationStatus
    connected_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    token_hint: str
    connected_by_name: str | None

    @classmethod
    def from_orm_connection(cls, connection, *, base_url: str, email: str) -> "ConfluenceConnectionRead":
        return cls(
            id=connection.id,
            integration_id=connection.integration_id,
            base_url=base_url,
            email=email,
            status=connection.status,
            connected_by_id=connection.connected_by_id,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
            token_hint=f"****{connection.token_last_four}",
            connected_by_name=connection.connected_by.full_name if connection.connected_by else None,
        )


class CreateConfluenceSpaceLinkRequest(BaseModel):
    project_id: uuid.UUID
    connection_id: uuid.UUID
    space_key: NonBlankStr = Field(..., max_length=50)


class ConfluenceSpaceLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    space_key: str
    space_name: str | None
    root_page_id: str
    root_page_url: str
    created_at: datetime
    updated_at: datetime


class ConfluencePageLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    confluence_space_link_id: uuid.UUID
    artifact_type: str
    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID
    confluence_page_id: str
    confluence_page_url: str
    confluence_page_title: str
    confluence_page_version: int
    created_at: datetime
    updated_at: datetime


class ConfluencePublishItemRead(BaseModel):
    artifact_type: str
    label: str
    artifact_id: uuid.UUID | None
    artifact_status: str | None
    content_preview: str
    validation_errors: list[str]
    already_published: ConfluencePageLinkRead | None
    update_available: bool

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0


class ConfluencePublishPreviewRead(BaseModel):
    project_id: uuid.UUID
    space_key: str
    items: list[ConfluencePublishItemRead]


class ConfluencePublishRequest(BaseModel):
    project_id: uuid.UUID
    triggered_by_user_id: uuid.UUID
    artifact_types: list[NonBlankStr] = Field(..., min_length=1)


class ConfluencePublishResultItem(BaseModel):
    artifact_type: str
    status: str  # "published" | "updated" | "skipped_invalid" | "failed"
    confluence_page_id: str | None = None
    confluence_page_url: str | None = None
    errors: list[str] = Field(default_factory=list)


class ConfluencePublishResponse(BaseModel):
    results: list[ConfluencePublishResultItem]
