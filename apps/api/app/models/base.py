"""Declarative base and shared mixins for all ORM models.

Model files are intentionally split one-per-concept (user, project, workflow,
artifact, review, agent, audit) and DO NOT import each other directly —
that would create an unmanageable import cycle, since almost every model
here references most of the others (a project has nodes, nodes have
artifacts, artifacts have reviews, reviews reference users, users own
projects, ...).

Instead, every model shares this module's `Base`, and `relationship(...)`
targets are given as plain strings (e.g. `relationship("User")`). SQLAlchemy
resolves those strings against `Base.registry` — the shared table of every
mapped class — once all model modules have been imported at least once
(see `app/models/__init__.py`), so no file needs to import its siblings.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable,
# predictable constraint/index names instead of DB-assigned ones.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key, generated application-side."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    """For append-only / immutable records (versions, comments, audit log)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    """For mutable records that track when they were last updated."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
