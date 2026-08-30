from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import IntegrationProvider, IntegrationStatus


class Integration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Configuration for one external-system connection agents will
    eventually reach through an MCP tool (see docs/architecture.md's MCP
    integrations section for the intended architecture).

    Foundation-only, per that plan: no real MCP client exists yet, no
    integration ever actually connects, and `config_json` never holds a
    real credential — a real implementation routes secrets through a
    vault/secret manager and stores only non-secret config here (workspace
    URL, project key, channel id, ...), never a token or password in this
    column. This model exists so Settings has somewhere real to read from
    and write to, and so connecting a real integration later is additive
    (new service + real status transitions), not a schema change.
    """

    __tablename__ = "integrations"

    integration_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(IntegrationProvider, native_enum=False, length=30, validate_strings=True), nullable=False
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, native_enum=False, length=20, validate_strings=True),
        default=IntegrationStatus.NOT_CONNECTED,
        nullable=False,
    )
    # Non-secret provider config only — see class docstring.
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Null until someone actually connects it — no seeded/placeholder row
    # has a connector, since none is really connected.
    connected_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Placeholder for a real sync timestamp once an integration actually
    # syncs data — always null today (see the UI's "Last sync" placeholder).
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    connected_by: Mapped["User | None"] = relationship("User", foreign_keys=[connected_by_id])
