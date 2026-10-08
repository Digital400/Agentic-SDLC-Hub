import uuid

from pydantic import BaseModel, Field


class BootstrapRepositoryRequest(BaseModel):
    triggered_by_user_id: uuid.UUID = Field(..., description="Existing user id — who triggered the bootstrap.")


class BootstrapCommitRead(BaseModel):
    path: str
    commit_sha: str


class BootstrapRepositoryResponse(BaseModel):
    repository_id: uuid.UUID
    branch: str
    commits: list[BootstrapCommitRead]
