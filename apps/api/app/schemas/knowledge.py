import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import KnowledgeContentType, KnowledgeSourceStatus, KnowledgeSourceType
from app.schemas.validators import NonBlankStr


class KnowledgeSourceCreate(BaseModel):
    title: NonBlankStr = Field(..., max_length=255)
    category: NonBlankStr = Field(..., max_length=100)
    source_type: KnowledgeSourceType
    file_url: str | None = Field(default=None, max_length=2048)
    uploaded_by_id: uuid.UUID = Field(..., description="Existing user id.")


class KnowledgeSourceUpdate(BaseModel):
    """Partial update — the only fields expected to change after
    creation while there's no real ingestion pipeline yet: correcting
    metadata, or manually moving status once a file actually lands."""

    title: NonBlankStr | None = Field(default=None, max_length=255)
    category: NonBlankStr | None = Field(default=None, max_length=100)
    file_url: str | None = Field(default=None, max_length=2048)
    status: KnowledgeSourceStatus | None = None


class KnowledgeSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    source_type: KnowledgeSourceType
    file_url: str | None
    status: KnowledgeSourceStatus
    uploaded_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Denormalized for list/detail views.
    uploaded_by_name: str
    chunk_count: int

    @classmethod
    def from_orm_source(cls, source, chunk_count: int | None = None) -> "KnowledgeSourceRead":
        return cls(
            id=source.id,
            title=source.title,
            category=source.category,
            source_type=source.source_type,
            file_url=source.file_url,
            status=source.status,
            uploaded_by_id=source.uploaded_by_id,
            created_at=source.created_at,
            updated_at=source.updated_at,
            uploaded_by_name=source.uploaded_by.full_name,
            chunk_count=len(source.chunks) if chunk_count is None else chunk_count,
        )


class KnowledgeChunkCreate(BaseModel):
    """No full ingestion pipeline (file parsing/chunking) exists yet — a
    chunk's `content` is supplied directly, e.g. by a future upload
    handler or by hand for fixtures. Its embedding is generated
    server-side on creation (see app/services/embeddings.py), never
    client-supplied — a chunk and its embedding must never disagree.

    stage/domain/project_type/content_type/tags are the retrieval-filtering
    metadata app/services/retrieval.py's stage-aware retrieval uses — see
    app/models/knowledge.py's KnowledgeChunk. All optional: a chunk with
    none of them set is stage-agnostic, untagged, and classified OTHER."""

    chunk_index: int = Field(..., ge=0)
    content: NonBlankStr
    metadata_json: dict[str, Any] | None = None
    stage: str | None = Field(default=None, max_length=100, description="A WorkflowNode.node_key, or null for every stage.")
    domain: str | None = Field(default=None, max_length=100)
    project_type: str | None = Field(default=None, max_length=100)
    content_type: KnowledgeContentType = KnowledgeContentType.OTHER
    tags: list[str] = Field(default_factory=list)


class KnowledgeChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    chunk_index: int
    content: str
    metadata_json: dict[str, Any] | None
    created_at: datetime
    stage: str | None
    domain: str | None
    project_type: str | None
    content_type: KnowledgeContentType
    tags: list[str]

    # The embedding vector itself is never returned — callers only need to
    # know retrieval can consider this chunk, not its raw coordinates.
    has_embedding: bool

    @classmethod
    def from_orm_chunk(cls, chunk) -> "KnowledgeChunkRead":
        return cls(
            id=chunk.id,
            source_id=chunk.source_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            metadata_json=chunk.metadata_json,
            created_at=chunk.created_at,
            stage=chunk.stage,
            domain=chunk.domain,
            project_type=chunk.project_type,
            content_type=chunk.content_type,
            tags=chunk.tags,
            has_embedding=chunk.embedding is not None,
        )


class KnowledgeSearchResult(BaseModel):
    """One hit from GET /knowledge-sources/search — a chunk plus enough of
    its source's identity to render a citation."""

    chunk_id: uuid.UUID
    source_id: uuid.UUID
    source_title: str
    chunk_index: int
    content: str
    similarity: float
    stage: str | None
    domain: str | None
    project_type: str | None
    content_type: KnowledgeContentType
    tags: list[str]
