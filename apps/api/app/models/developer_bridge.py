from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    BridgeDeviceAuthorizationStatus,
    BridgeJobAssignmentStatus,
    BridgeSessionStatus,
)


class BridgeDeviceAuthorization(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One OAuth 2.0 Device Authorization Grant (RFC 8628) attempt from a
    Developer Bridge CLI instance. `device_code` is the value the CLI polls
    with; `user_code` is the short value shown to the developer to enter
    into an approval page. Nothing here ever stores a runtime credential —
    only this platform's own short-lived `access_token`, scoped to the
    Developer Bridge API surface alone (app/api/routes/developer_bridge.py).
    """

    __tablename__ = "bridge_device_authorizations"

    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    device_code: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    user_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    status: Mapped[BridgeDeviceAuthorizationStatus] = mapped_column(
        Enum(BridgeDeviceAuthorizationStatus), nullable=False, default=BridgeDeviceAuthorizationStatus.PENDING
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    access_token: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approved_by: Mapped["User | None"] = relationship("User")


class BridgeSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tracks one developer's Developer Bridge connection status —
    "the server must track connected/offline bridge status" (Phase 13's
    own literal requirement). One row per developer; `last_seen_at` is
    updated on every status report.
    """

    __tablename__ = "bridge_sessions"

    developer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    status: Mapped[BridgeSessionStatus] = mapped_column(
        Enum(BridgeSessionStatus), nullable=False, default=BridgeSessionStatus.OFFLINE
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    developer: Mapped["User"] = relationship("User")


class BridgeJobAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One unit of work assigned to run on a developer's own machine via
    the Developer Bridge, instead of the company sandbox. Scoped to an
    ImplementationTask exactly like the company-sandbox path already is
    (app/models/implementation_task.py) — the bridge is an alternative
    execution location for the same task graph, not a second task system.

    `evidence_trusted` is always False on upload — "treat local evidence
    as untrusted until CI verification" (Phase 13's own literal
    requirement). Nothing in this model marks it True; a future CI
    integration is the only thing that may ever flip it.
    """

    __tablename__ = "bridge_job_assignments"

    implementation_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("implementation_tasks.id", ondelete="CASCADE"), nullable=False
    )
    developer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    runtime_key: Mapped[str] = mapped_column(String(200), nullable=False)
    repository_remote_url: Mapped[str] = mapped_column(String(500), nullable=False)
    repository_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    repository_base_commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[BridgeJobAssignmentStatus] = mapped_column(
        Enum(BridgeJobAssignmentStatus), nullable=False, default=BridgeJobAssignmentStatus.ASSIGNED
    )
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence: Mapped[dict | None] = mapped_column("evidence_json", JSON, nullable=True)
    evidence_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    implementation_task: Mapped["ImplementationTask"] = relationship("ImplementationTask")
    developer: Mapped["User"] = relationship("User")
