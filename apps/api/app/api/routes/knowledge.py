"""Knowledge Base endpoints.

Covers: register a knowledge source, upload a document (extracted,
chunked, and embedded in one step — see
app/services/document_ingestion.py), list/get/update/delete sources,
list/create the chunks under one source, (re)generate their embeddings,
and semantic search across all chunks. Retrieval for agent runs itself
lives in app/services/retrieval.py — this file's search endpoint is the
same underlying query, exposed directly for standalone use (e.g. the
Knowledge Base search UI, or debugging what a run would retrieve).
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import KnowledgeChunk, KnowledgeContentType, KnowledgeSource, KnowledgeSourceStatus, KnowledgeSourceType, Project, User
from app.schemas.knowledge import (
    KnowledgeChunkCreate,
    KnowledgeChunkRead,
    KnowledgeSearchResult,
    KnowledgeSourceCreate,
    KnowledgeSourceFromTextCreate,
    KnowledgeSourceRead,
    KnowledgeSourceUpdate,
)
from app.services.audit import record_audit_log
from app.services.document_ingestion import DocumentIngestionError, chunk_text, extract_text
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


def _get_project_or_400(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"project_id {project_id} does not match an existing project")
    return project


# 1. Register a knowledge source --------------------------------------------------


@router.post("", response_model=KnowledgeSourceRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_source(payload: KnowledgeSourceCreate, db: Session = Depends(get_db)) -> KnowledgeSourceRead:
    uploaded_by = _get_user_or_400(db, payload.uploaded_by_id, "uploaded_by_id")
    if payload.project_id is not None:
        _get_project_or_400(db, payload.project_id)

    source = KnowledgeSource(
        title=payload.title,
        category=payload.category,
        source_type=payload.source_type,
        file_url=payload.file_url,
        status=KnowledgeSourceStatus.PENDING,
        uploaded_by=uploaded_by,
        project_id=payload.project_id,
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


# 1b. Upload a document as a new knowledge source ------------------------------------


@router.post("/upload", response_model=KnowledgeSourceRead, status_code=status.HTTP_201_CREATED)
async def upload_knowledge_source(
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    title: str = Form(...),
    category: str = Form(...),
    uploaded_by_id: uuid.UUID = Form(...),
    content_type: KnowledgeContentType = Form(default=KnowledgeContentType.OTHER),
    stage: str | None = Form(default=None),
    domain: str | None = Form(default=None),
    project_type: str | None = Form(default=None),
    tags: str = Form(default="", description="Comma-separated."),
) -> KnowledgeSourceRead:
    """Extracts the uploaded file's text (see
    app/services/document_ingestion.py — .txt/.md/.pdf), splits it into
    chunks, and embeds each one immediately — one call instead of
    "create a source" + "create each chunk by hand" for a real document.
    The source lands straight in INDEXED (matching what
    generate-embeddings does for a manually-built source), since every
    chunk is embedded before this returns.

    The uploaded file's raw bytes aren't stored anywhere (no object
    storage is configured in this app) — only its extracted, chunked text
    is persisted. `file_url` is set to the original filename as a plain
    label, not a real location.
    """
    uploaded_by = _get_user_or_400(db, uploaded_by_id, "uploaded_by_id")

    raw_bytes = await file.read()
    try:
        text = extract_text(filename=file.filename or "upload", raw_bytes=raw_bytes)
    except DocumentIngestionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    chunk_contents = chunk_text(text)
    if not chunk_contents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file has no extractable text content.")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    source = KnowledgeSource(
        title=title,
        category=category,
        source_type=KnowledgeSourceType.UPLOADED_DOCUMENT,
        file_url=file.filename,
        status=KnowledgeSourceStatus.PENDING,
        uploaded_by=uploaded_by,
    )
    db.add(source)
    db.flush()

    for i, content in enumerate(chunk_contents):
        db.add(
            KnowledgeChunk(
                source=source,
                chunk_index=i,
                content=content,
                stage=stage,
                domain=domain,
                project_type=project_type,
                content_type=content_type,
                tags=tag_list,
                embedding=embed_text(content),
            )
        )
    source.status = KnowledgeSourceStatus.INDEXED
    db.flush()

    record_audit_log(
        db,
        actor_user_id=uploaded_by.id,
        action="knowledge_source.uploaded",
        entity_type="KnowledgeSource",
        entity_id=source.id,
        extra_data={"filename": file.filename, "chunk_count": len(chunk_contents), "content_type": content_type.value},
    )

    db.commit()
    db.refresh(source)
    return KnowledgeSourceRead.from_orm_source(source)


# 1c. Create a source + its one chunk from pasted text (no file) ---------------------


@router.post("/from-text", response_model=KnowledgeSourceRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_source_from_text(
    payload: KnowledgeSourceFromTextCreate, db: Session = Depends(get_db)
) -> KnowledgeSourceRead:
    """Pasted-content counterpart to POST /knowledge-sources/upload —
    creates a source and chunks+embeds its text in one call, landing
    straight in INDEXED like that endpoint does. Optionally scoped to a
    project via `project_id` (see KnowledgeSource.project_id / the
    module docstring) — used by the Create Project wizard's Knowledge
    Base step, and usable standalone for org-wide notes too."""
    uploaded_by = _get_user_or_400(db, payload.uploaded_by_id, "uploaded_by_id")
    if payload.project_id is not None:
        _get_project_or_400(db, payload.project_id)

    chunk_contents = chunk_text(payload.content)
    if not chunk_contents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "content has no extractable text.")

    source = KnowledgeSource(
        title=payload.title,
        category=payload.category,
        source_type=payload.source_type,
        file_url=payload.file_url,
        status=KnowledgeSourceStatus.PENDING,
        uploaded_by=uploaded_by,
        project_id=payload.project_id,
    )
    db.add(source)
    db.flush()

    for i, content in enumerate(chunk_contents):
        db.add(KnowledgeChunk(source=source, chunk_index=i, content=content, embedding=embed_text(content)))
    source.status = KnowledgeSourceStatus.INDEXED
    db.flush()

    record_audit_log(
        db,
        project_id=payload.project_id,
        actor_user_id=uploaded_by.id,
        action="knowledge_source.created_from_text",
        entity_type="KnowledgeSource",
        entity_id=source.id,
        extra_data={"title": source.title, "chunk_count": len(chunk_contents), "project_scoped": payload.project_id is not None},
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
    project_id: uuid.UUID | None = Query(default=None, description="Exact match — omit for every source (global + every project's)."),
) -> list[KnowledgeSourceRead]:
    query = db.query(KnowledgeSource)
    if category is not None:
        query = query.filter(KnowledgeSource.category == category)
    if status_filter is not None:
        query = query.filter(KnowledgeSource.status == status_filter)
    if project_id is not None:
        query = query.filter(KnowledgeSource.project_id == project_id)

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
    stage: str | None = Query(default=None, description="A WorkflowNode.node_key — matches that stage or stage-agnostic (null-stage) chunks, same rule as automatic retrieval."),
    content_type: KnowledgeContentType | None = Query(default=None),
    domain: str | None = Query(default=None),
    project_type: str | None = Query(default=None),
) -> list[KnowledgeSearchResult]:
    """Cosine-similarity search over every chunk with an embedding — the
    same underlying query app/services/retrieval.py runs before an agent
    run, exposed directly (with the same stage-aware rule when `stage` is
    given). Results below the same relevance threshold used there are
    excluded, so an unrelated query legitimately returns []."""
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
    if stage is not None:
        q = q.filter((KnowledgeChunk.stage.is_(None)) | (KnowledgeChunk.stage == stage))
    if content_type is not None:
        q = q.filter(KnowledgeChunk.content_type == content_type)
    if domain is not None:
        q = q.filter(KnowledgeChunk.domain == domain)
    if project_type is not None:
        q = q.filter(KnowledgeChunk.project_type == project_type)

    rows = q.order_by(distance).limit(limit).all()

    return [
        KnowledgeSearchResult(
            chunk_id=chunk.id,
            source_id=source.id,
            source_title=source.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            similarity=round(1.0 - float(dist), 4),
            stage=chunk.stage,
            domain=chunk.domain,
            project_type=chunk.project_type,
            content_type=chunk.content_type,
            tags=chunk.tags,
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
        stage=payload.stage,
        domain=payload.domain,
        project_type=payload.project_type,
        content_type=payload.content_type,
        tags=payload.tags,
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
