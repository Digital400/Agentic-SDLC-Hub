from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import KnowledgeContentType, KnowledgeSourceStatus, KnowledgeSourceType
from app.services.embeddings import EMBEDDING_DIM


class KnowledgeSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One document/link an agent could eventually retrieve from — the
    Knowledge Base's top-level entity.

    This is foundation-only: nothing here is actually chunked, embedded, or
    retrieved yet (see docs/mvp-plan.md — RAG is roadmap, not built). This
    model exists so the ingestion pipeline has somewhere to write to once
    it's built, without a schema migration blocking that work. `status`
    tracks that future pipeline's progress; every source is created PENDING
    and nothing currently advances it further.
    """

    __tablename__ = "knowledge_sources"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Freeform, not an enum — categories are expected to evolve (e.g. by
    # workspace/team) faster than a schema migration should gate them.
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[KnowledgeSourceType] = mapped_column(
        Enum(KnowledgeSourceType, native_enum=False, length=30, validate_strings=True), nullable=False
    )
    # Where the underlying file/link actually lives. Nullable because
    # nothing uploads real files yet (see the frontend's "Upload document"
    # placeholder button) — a source can be registered before its file is.
    file_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[KnowledgeSourceStatus] = mapped_column(
        Enum(KnowledgeSourceStatus, native_enum=False, length=20, validate_strings=True),
        default=KnowledgeSourceStatus.PENDING,
        nullable=False,
    )
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Null = org-wide/global (visible to every project's retrieval — the
    # original behavior). Non-null scopes this source to exactly one
    # project — see app/services/retrieval.py, which matches sources
    # where project_id IS NULL OR project_id = the run's project.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )

    uploaded_by: Mapped["User"] = relationship("User", foreign_keys=[uploaded_by_id])
    project: Mapped["Project | None"] = relationship("Project")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.chunk_index",
    )


class KnowledgeChunk(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One retrieval-sized slice of a KnowledgeSource's content.

    Immutable and append-only, like ArtifactVersion/AuditLog — a chunk is
    what a future re-chunking pass would replace wholesale, not edit in
    place. `embedding` is a real pgvector column (see the `vector` Postgres
    extension, enabled by this feature's migration) queried via cosine
    distance for retrieval — see app/services/retrieval.py. It's nullable
    because a chunk can be registered before embedding generation runs
    (`POST /knowledge-sources/{id}/generate-embeddings`); a null embedding
    is simply invisible to retrieval rather than an error.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index"),
        # HNSW: supports cosine distance directly and needs no training
        # step (unlike ivfflat), which suits a table that starts near-empty.
        # Created by hand in the migration (op.execute) rather than via
        # Alembic's own DDL, since Alembic's autogenerate can't emit
        # `USING hnsw (... vector_cosine_ops)` — declared here purely so
        # autogenerate recognizes it as expected and doesn't propose
        # dropping it on the next `alembic revision --autogenerate`.
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Freeform per-chunk metadata (e.g. page number, section heading) —
    # shape is intentionally undefined until a real chunking pipeline
    # decides what's useful to carry.
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    # --- Retrieval-filtering metadata (see app/services/retrieval.py) ------------
    #
    # All five are used for stage-aware, targeted retrieval rather than
    # forced into metadata_json, since they're first-class filter/display
    # dimensions now, not incidental chunking detail.
    #
    # A WorkflowNode.node_key (e.g. "hld", "testing") this chunk is most
    # relevant to — null means stage-agnostic (eligible for every stage),
    # e.g. a company-wide coding standard. Non-null narrows a chunk to
    # only the stage(s) that would actually use it, so a UI-guideline
    # chunk doesn't surface during, say, Infrastructure Provisioning.
    stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Freeform subject-matter tag (e.g. "engineering", "security", "ux") —
    # informational/filterable, not hard-filtered by automatic retrieval.
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Freeform project archetype this chunk is written for (e.g. "web-app",
    # "mobile", "api") — null means generic/applies to any project type.
    project_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # What KIND of guidance this is — see KnowledgeContentType. The fixed
    # set RAG is required to support: company standards, past artifacts,
    # UI guidelines, architecture rules, testing standards.
    content_type: Mapped[KnowledgeContentType] = mapped_column(
        Enum(KnowledgeContentType, native_enum=False, length=30, validate_strings=True),
        default=KnowledgeContentType.OTHER,
        nullable=False,
    )
    # Additional freeform labels for search/filtering beyond the four
    # structured fields above (e.g. ["accessibility", "checkout-flow"]).
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    source: Mapped["KnowledgeSource"] = relationship("KnowledgeSource", back_populates="chunks")
