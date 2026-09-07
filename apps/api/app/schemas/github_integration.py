import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IntegrationStatus, RepositoryFileEntryType
from app.schemas.validators import NonBlankStr


class ConnectGitHubRequest(BaseModel):
    """Body for POST /github/connections. `access_token` is write-only —
    it is verified against GitHub, encrypted, and stored; it is never
    echoed back in this or any other response (see
    IntegrationConnectionRead, which deliberately has no token field)."""

    access_token: NonBlankStr = Field(..., description="A GitHub personal access token with read-only repo access.")
    connected_by_id: uuid.UUID = Field(..., description="Existing user id.")


class IntegrationConnectionRead(BaseModel):
    """Deliberately has NO token field, encrypted or otherwise — see
    IntegrationConnection's own docstring. `token_hint` (e.g. "****d3f9")
    is the only thing a UI can show about the stored credential."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_id: uuid.UUID
    github_username: str | None
    scopes: list[str] | None
    status: IntegrationStatus
    connected_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    token_hint: str
    connected_by_name: str | None

    @classmethod
    def from_orm_connection(cls, connection) -> "IntegrationConnectionRead":
        return cls(
            id=connection.id,
            integration_id=connection.integration_id,
            github_username=connection.github_username,
            scopes=connection.scopes,
            status=connection.status,
            connected_by_id=connection.connected_by_id,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
            token_hint=f"****{connection.token_last_four}",
            connected_by_name=connection.connected_by.full_name if connection.connected_by else None,
        )


class GitHubRepoSummaryRead(BaseModel):
    """One repo option for the picker — see
    app/services/github_integration.py's list_repositories. Not persisted;
    built fresh from GitHub's own response on every call."""

    owner: str
    name: str
    full_name: str
    default_branch: str
    description: str | None
    is_private: bool
    html_url: str


class CreateRepositoryRequest(BaseModel):
    project_id: uuid.UUID
    connection_id: uuid.UUID
    owner: NonBlankStr = Field(..., max_length=255)
    name: NonBlankStr = Field(..., max_length=255)


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    owner: str
    name: str
    default_branch: str | None
    description: str | None
    html_url: str | None
    is_private: bool | None
    is_primary: bool
    created_at: datetime
    updated_at: datetime


class RepositoryTreeEntryRead(BaseModel):
    path: str
    entry_type: str
    size: int | None
    sha: str


class RepositoryTreeRead(BaseModel):
    commit_sha: str
    entries: list[RepositoryTreeEntryRead]
    truncated: bool


class RepositoryFileContentRead(BaseModel):
    path: str
    sha: str
    size: int
    content: str | None
    truncated: bool
    is_binary: bool


class CreateSnapshotRequest(BaseModel):
    triggered_by_id: uuid.UUID = Field(..., description="Existing user id.")
    ref: str | None = Field(default=None, description="Branch/tag/sha to snapshot; defaults to the repo's default branch.")


class RepositorySnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    ref: str
    commit_sha: str
    file_count: int
    truncated: bool
    triggered_by_id: uuid.UUID | None
    created_at: datetime

    triggered_by_name: str | None

    @classmethod
    def from_orm_snapshot(cls, snapshot) -> "RepositorySnapshotRead":
        return cls(
            id=snapshot.id,
            repository_id=snapshot.repository_id,
            ref=snapshot.ref,
            commit_sha=snapshot.commit_sha,
            file_count=snapshot.file_count,
            truncated=snapshot.truncated,
            triggered_by_id=snapshot.triggered_by_id,
            created_at=snapshot.created_at,
            triggered_by_name=snapshot.triggered_by.full_name if snapshot.triggered_by else None,
        )


class RepositoryFileIndexRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    snapshot_id: uuid.UUID
    path: str
    entry_type: RepositoryFileEntryType
    size: int | None
    sha: str
