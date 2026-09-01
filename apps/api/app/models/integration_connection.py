from __future__ import annotations

import uuid

from sqlalchemy import JSON, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import IntegrationStatus


class IntegrationConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A real, credentialed connection for one `Integration` row — see
    app/models/integration.py. Org-wide, like `Integration` itself (no
    `project_id`; one company GitHub PAT can back multiple projects'
    `Repository` configs) — not the per-project `Repository` model below.

    SECURITY: `docs/architecture.md`'s MCP integrations section documents
    the intended end state as "credentials in a vault... never persisted
    in this table or logged." No vault exists anywhere in this codebase
    yet. This is a deliberate, disclosed bridge (see
    app/core/security.py's own docstring): `access_token_encrypted` is
    Fernet-ciphertext, never plaintext — but it is still an app-managed
    secret in Postgres, not a vault-resolved one. It must NEVER be
    included in any Pydantic response schema, any audit log's
    `extra_data`, or any log line — see app/services/github_integration.py
    and app/api/routes/github_integration.py, which decrypt it only
    transiently, once per outbound call, and never persist the plaintext
    anywhere else.
    """

    __tablename__ = "integration_connections"

    integration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("integrations.id"), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Display-only hint (e.g. "****d3f9") — never the real value, and never
    # enough on its own to reconstruct or narrow down the real token.
    token_last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    # Populated from GitHub's own GET /user response and its X-OAuth-Scopes
    # response header at connect time — informational only, never used to
    # enforce anything (this app never requests write/push scopes, and
    # doesn't need to verify what a token grants beyond "it authenticates").
    github_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    connected_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, native_enum=False, length=20, validate_strings=True),
        default=IntegrationStatus.NOT_CONNECTED,
        nullable=False,
    )

    integration: Mapped["Integration"] = relationship("Integration")
    connected_by: Mapped["User | None"] = relationship("User", foreign_keys=[connected_by_id])
