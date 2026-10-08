"""coding standard source url

Revision ID: c3a91e7d5b20
Revises: b6f2a1c9d0e4
Create Date: 2026-09-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a91e7d5b20'
down_revision: Union[str, None] = 'b6f2a1c9d0e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hand-written. Nullable, so every existing coding standard is unchanged.
    op.add_column('project_coding_standards', sa.Column('source_url', sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column('project_coding_standards', 'source_url')
