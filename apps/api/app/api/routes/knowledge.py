"""Knowledge Base endpoints.

Covers: register a knowledge source, list/get/update/delete sources,
list/create the chunks under one source, (re)generate their embeddings,
and semantic search across all chunks. Retrieval for agent runs itself
lives in app/services/retrieval.py — this file's search endpoint is the
same underlying query, exposed directly for standalone use (e.g. a future
Knowledge Base search UI, or debugging what a run would retrieve).

No file-upload/parsing/chunking pipeline exists yet — see
app/models/knowledge.py's module docstring — so a chunk's `content` is
still supplied directly rather than extracted from an uploaded file.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import KnowledgeChunk, KnowledgeSource, KnowledgeSourceStatus, User
from app.schemas.knowledge import (
    KnowledgeChunkCreate,
    KnowledgeChunkRead,
    KnowledgeSearchResult,
    KnowledgeSourceCreate,
    KnowledgeSourceRead,
    KnowledgeSourceUpdate,
)
from app.services.audit import record_audit_log
from app.services.embeddings import embed_text
from app.services.retrieval import MAX_COSINE_DISTANCE

router = APIRouter(prefix="/knowledge-sources", tags=["knowledge-base"])


def _get_source_or_404(db: Session, source_id: uuid.UUID) -> KnowledgeSource:
    source = db.get(KnowledgeSource, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Knowledge source {source_id} not found")
    return source


def _get_user_or_400(db: Session, user_id: uuid.UUID, field_name: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field_name} {user_id} does not match an existing user")
    return user


# 1. Register a knowledge source --------------------------------------------------


@router.post("", response_model=KnowledgeSourceRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_source(payload: KnowledgeSourceCreate, db: Session = Depends(get_db)) -> KnowledgeSourceRead:
    uploaded_by = _get_user_or_400(db, payload.uploaded_by_id, "uploaded_by_id")

    source = KnowledgeSource(
        title=payload.title,
        category=payload.category,
        source_type=payload.source_type,
        file_url=payload.file_url,
        status=KnowledgeSourceStatus.PENDING,
        uploaded_by=uploaded_by,
    )
    db.add(source)
    db.flush()

    record_audit_log(
        db,
        actor_user_id=uploaded_by.id,
        action="knowledge_source.created",
        entity_type="KnowledgeSource",
        entity_id=source.id,
        extra_data={"title": source.title, "category": source.category, "source_type": source.source_type.value},
    )

    db.commit()
    db.refresh(source)
    return KnowledgeSourceRead.from_orm_source(source)


# 2. List knowledge sources --------------------------------------------------------


@router.get("", response_model=list[KnowledgeSourceRead])
def list_knowledge_sources(
    db: Session = Depends(get_db),
    category: str | None = Query(default=None),
    status_filter: KnowledgeSourceStatus | None = Query(default=None, alias="status"),
) -> list[KnowledgeSourceRead]:
    query = db.query(KnowledgeSource)
    if category is not None:
        query = query.filter(KnowledgeSource.category == category)
    if status_filter is not None:
        query = query.filter(KnowledgeSource.status == status_filter)

    sources = query.order_by(KnowledgeSource.created_at.desc()).all()
    return [KnowledgeSourceRead.from_orm_source(s) for s in sources]


# 3. Semantic search --------------------------------------------------------------
#
# Registered before "/{source_id}" so "/knowledge-sources/search" doesn't
# get swallowed by that path parameter — FastAPI matches routes in
# declaration order.


@router.get("/search", response_model=list[KnowledgeSearchResult])
def search_knowledge(
    db: Session = Depends(get_db),
    query: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    category: str | None = Query(default=None),
) -> list[KnowledgeSearchResult]:
    """Cosine-similarity search over every chunk with an embedding — the
    same underlying query app/services/retrieval.py runs before an agent
    run, exposed directly. Results below the same relevance threshold used
    there are excluded, so an unrelated query legitimately returns []."""
    query_vector = embed_text(query)
    distance = KnowledgeChunk.embedding.cosine_distance(query_vector)

    q = (
        db.query(KnowledgeChunk, KnowledgeSource, distance.label("distance"))
        .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
        .filter(KnowledgeChunk.embedding.is_not(None))
        .filter(distance < MAX_COSINE_DISTANCE)
    )
    if category is not None:
        q = q.filter(KnowledgeSource.category == category)

    rows = q.order_by(distance).limit(limit).all()

    return [
        KnowledgeSearchResult(
            chunk_id=chunk.id,
            source_id=source.id,
            source_title=source.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            similarity=round(1.0 - float(dist), 4),
        )
        for chunk, source, dist in rows
    ]


# 4. Get knowledge source by id -----------------------------------------------------


@router.get("/{source_id}", response_model=KnowledgeSourceRead)
def get_knowledge_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> KnowledgeSourceRead:
    return KnowledgeSourceRead.from_orm_source(_get_source_or_404(db, source_id))


# 5. Update knowledge source ------------------------------------------------------


@router.patch("/{source_id}", response_model=KnowledgeSourceRead)
def update_knowledge_source(
    source_id: uuid.UUID, payload: KnowledgeSourceUpdate, db: Session = Depends(get_db)
) -> KnowledgeSourceRead:
    source = _get_source_or_404(db, source_id)

    changes: dict[str, object] = {}
    for field in ("title", "category", "file_url", "status"):
        value = getattr(payload, field)
        if value is not None:
            changes[field] = value.value if isinstance(value, KnowledgeSourceStatus) else value
            setattr(source, field, value)

    if changes:
        record_audit_log(
            db,
            action="knowledge_source.updated",
            entity_type="KnowledgeSource",
            entity_id=source.id,
            extra_data={"fields": changes},
        )

    db.commit()
    db.refresh(source)
    return KnowledgeSourceRead.from_orm_source(source)


# 6. Delete knowledge source -------------------------------------------------------


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    source = _get_source_or_404(db, source_id)

    record_audit_log(
        db,
        action="knowledge_source.deleted",
        entity_type="KnowledgeSource",
        entity_id=source.id,
        extra_data={"title": source.title},
    )

    db.delete(source)
    db.commit()


# 7. List chunks for a source -----------------------------------------------------


@router.get("/{source_id}/chunks", response_model=list[KnowledgeChunkRead])
def list_knowledge_chunks(source_id: uuid.UUID, db: Session = Depends(get_db)) -> list[KnowledgeChunkRead]:
    _get_source_or_404(db, source_id)
    chunks = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.source_id == source_id)
        .order_by(KnowledgeChunk.chunk_index)
        .all()
    )
    return [KnowledgeChunkRead.from_orm_chunk(c) for c in chunks]


# 8. Create a chunk under a source -------------------------------------------------


@router.post("/{source_id}/chunks", response_model=KnowledgeChunkRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_chunk(
    source_id: uuid.UUID, payload: KnowledgeChunkCreate, db: Session = Depends(get_db)
) -> KnowledgeChunkRead:
    source = _get_source_or_404(db, source_id)

    existing = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.source_id == source_id, KnowledgeChunk.chunk_index == payload.chunk_index)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Chunk index {payload.chunk_index} already exists for this source."
        )

    chunk = KnowledgeChunk(
        source=source,
        chunk_index=payload.chunk_index,
        content=payload.content,
        metadata_json=payload.metadata_json,
        # Generated immediately, not left for a separate step — see
        # KnowledgeChunkCreate's docstring on why this is never client-supplied.
        embedding=embed_text(payload.content),
    )
    db.add(chunk)
    db.flush()

    record_audit_log(
        db,
        action="knowledge_chunk.created",
        entity_type="KnowledgeChunk",
        entity_id=chunk.id,
        extra_data={"source_id": str(source.id), "chunk_index": chunk.chunk_index},
    )

    db.commit()
    db.refresh(chunk)
    return KnowledgeChunkRead.from_orm_chunk(chunk)


# 9. (Re)generate embeddings for a source's chunks ---------------------------------


@router.post("/{source_id}/generate-embeddings", response_model=KnowledgeSourceRead)
def generate_source_embeddings(
    source_id: uuid.UUID, db: Session = Depends(get_db), force: bool = Query(default=False)
) -> KnowledgeSourceRead:
    """Embeds every chunk under this source that doesn't have an embedding
    yet (or all of them, with `force=true` — e.g. after a chunk's content
    changed). Marks the source INDEXED once it has at least one embedded
    chunk. This is the "1. Generate embeddings for knowledge chunks" /
    "2. Store embeddings in pgvector" step — manual for now since there's
    no upload pipeline to trigger it automatically yet."""
    source = _get_source_or_404(db, source_id)

    query = db.query(KnowledgeChunk).filter(KnowledgeChunk.source_id == source_id)
    if not force:
        query = query.filter(KnowledgeChunk.embedding.is_(None))
    chunks = query.all()

    for chunk in chunks:
        chunk.embedding = embed_text(chunk.content)
    db.flush()

    has_any_embedded = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.source_id == source_id, KnowledgeChunk.embedding.is_not(None))
        .first()
        is not None
    )
    if has_any_embedded:
        source.status = KnowledgeSourceStatus.INDEXED

    record_audit_log(
        db,
        action="knowledge_source.embeddings_generated",
        entity_type="KnowledgeSource",
        entity_id=source.id,
        extra_data={"chunks_embedded": len(chunks), "force": force},
    )

    db.commit()
    db.refresh(source)
    return KnowledgeSourceRead.from_orm_source(source)
