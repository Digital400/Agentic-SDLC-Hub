"""add pgvector embeddings and agent run retrieval

Revision ID: 7796bda4707f
Revises: 6f0d0623a2b3
Create Date: 2026-08-30 03:53:58.664352

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = '7796bda4707f'
down_revision: Union[str, None] = '6f0d0623a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Requires the postgresql-16-pgvector OS package installed alongside
    # the server (see docs/mvp-plan.md's RAG section) — this just enables
    # the extension once that package is present.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column('agent_runs', sa.Column('retrieved_sources', sa.JSON(), nullable=True))

    # Every existing row's embedding is NULL (no embedding pipeline existed
    # before this migration), so there's no real JSON->vector data to
    # convert — USING NULL keeps the ALTER a no-op on data, just a type swap.
    op.alter_column(
        'knowledge_chunks', 'embedding',
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=Vector(384),
        existing_nullable=True,
        postgresql_using="NULL",
    )

    # HNSW supports cosine distance directly and needs no training step
    # (unlike ivfflat), which suits a table that starts near-empty.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.alter_column(
        'knowledge_chunks', 'embedding',
        existing_type=Vector(384),
        type_=postgresql.JSON(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="NULL",
    )
    op.drop_column('agent_runs', 'retrieved_sources')
    # Extension intentionally left installed on downgrade — dropping it
    # would be destructive if anything else in the database came to depend
    # on the `vector` type, and CREATE EXTENSION IF NOT EXISTS is idempotent
    # either way.
