from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RoleAssignmentScope, RuntimeRole


class RuntimeRoleAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One user's grant of one RuntimeRole, either organization-wide
    (`scope == ORGANIZATION`, `project_id is None`) or for exactly one
    project (`scope == PROJECT`, `project_id` set) — see
    app/runtime_security/rbac.py's RBACService, the only code that should
    read/write these rows.

    THE PROJECT-ISOLATION MECHANISM: a PROJECT-scoped assignment is what
    makes "is this actor actually allowed to act on THIS project" a real,
    checked question — distinct from (and in addition to) whether they
    hold a role at all. This is deliberately separate from
    app/models/project.py's existing ProjectMember/ProjectRole pair,
    which the Phase 00 baseline found is written once at project creation
    and never read or enforced by anything — see
    app/runtime_security/__init__.py's module docstring for why this
    phase adds a new, actually-enforced table instead of retrofitting
    that dormant one.
    """

    __tablename__ = "runtime_role_assignments"
    __table_args__ = (UniqueConstraint("user_id", "scope", "project_id", "role", name="uq_runtime_role_assignments_user_scope_project_role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[RoleAssignmentScope] = mapped_column(
        Enum(RoleAssignmentScope, native_enum=False, length=15, validate_strings=True), nullable=False,
    )
    # NULL for an ORGANIZATION-scoped assignment; required for a
    # PROJECT-scoped one — enforced in app/runtime_security/rbac.py, not
    # by a DB CHECK constraint (this codebase's existing convention —
    # e.g. AgentPrompt.is_active — already accepts an application-enforced
    # invariant here rather than a partial/conditional constraint).
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    role: Mapped[RuntimeRole] = mapped_column(
        Enum(RuntimeRole, native_enum=False, length=20, validate_strings=True), nullable=False,
    )
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    project: Mapped["Project | None"] = relationship("Project", foreign_keys=[project_id])
    granted_by: Mapped["User | None"] = relationship("User", foreign_keys=[granted_by_id])
