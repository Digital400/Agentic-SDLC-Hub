"""RBACService — resolves an AuthenticatedActor's RuntimeRole(s) for a
given project, and is the ONE place "is this actor even allowed to touch
this project at all" (project isolation) is decided.

PROJECT ISOLATION, PRECISELY: an actor has a role on project P if and
only if there is a RuntimeRoleAssignment row that is either (a) scoped
ORGANIZATION for that user (applies everywhere), or (b) scoped PROJECT
with project_id == P for that user. Holding a role on project A confers
NOTHING on project B — there is no implicit "any assignment counts
everywhere" fallback. ADMIN is the only role with an organization-wide
bypass by DESIGN (see has_role's docstring), mirroring
app/services/permissions.py's own existing ADMIN-always-passes
convention (Phase 00 baseline section 10) — but even ADMIN requires a
real ORGANIZATION-scoped assignment row to exist; it is not free-floating
off the legacy UserRole.ADMIN value.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import RoleAssignmentScope, RuntimeRole, RuntimeRoleAssignment


class RBACService:
    def __init__(self, db: Session):
        self.db = db

    def roles_for(self, user_id: uuid.UUID, *, project_id: uuid.UUID | None) -> set[RuntimeRole]:
        """Every role this user effectively holds for `project_id` —
        their organization-wide assignments UNION their assignments
        scoped specifically to this project. `project_id=None` returns
        only organization-wide roles (no project context to check
        against)."""
        query = self.db.query(RuntimeRoleAssignment.role).filter(RuntimeRoleAssignment.user_id == user_id)
        if project_id is not None:
            query = query.filter(
                (RuntimeRoleAssignment.scope == RoleAssignmentScope.ORGANIZATION)
                | ((RuntimeRoleAssignment.scope == RoleAssignmentScope.PROJECT) & (RuntimeRoleAssignment.project_id == project_id))
            )
        else:
            query = query.filter(RuntimeRoleAssignment.scope == RoleAssignmentScope.ORGANIZATION)
        return {row[0] for row in query.all()}

    def has_role(self, user_id: uuid.UUID, role: RuntimeRole, *, project_id: uuid.UUID | None) -> bool:
        """True if the user holds `role` for `project_id` specifically,
        OR holds RuntimeRole.ADMIN (organization- or project-scoped) —
        ADMIN is the only role that satisfies every other role's check,
        matching app/services/permissions.py's own existing "ADMIN always
        passes" convention. Every other role check is exact — REVIEWER
        does not imply DEVELOPER, etc."""
        held = self.roles_for(user_id, project_id=project_id)
        return role in held or RuntimeRole.ADMIN in held

    def is_project_member(self, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        """True if the user holds ANY role — organization-wide, or
        specifically on this project — that would let them act on this
        project at all. False means "not even a Viewer here": this is the
        actual project-isolation gate (see module docstring); a caller
        should check this BEFORE any specific role check, so an
        unauthorized project access is reported as "you have no access to
        this project," not as "you lack the specific role for this
        action" (which would leak that the project exists and what roles
        it has configured)."""
        return bool(self.roles_for(user_id, project_id=project_id))

    def grant(self, *, user_id: uuid.UUID, role: RuntimeRole, scope: RoleAssignmentScope, project_id: uuid.UUID | None, granted_by_id: uuid.UUID | None) -> RuntimeRoleAssignment:
        if scope == RoleAssignmentScope.PROJECT and project_id is None:
            raise ValueError("A PROJECT-scoped grant requires project_id.")
        if scope == RoleAssignmentScope.ORGANIZATION and project_id is not None:
            raise ValueError("An ORGANIZATION-scoped grant must not set project_id.")

        assignment = RuntimeRoleAssignment(user_id=user_id, role=role, scope=scope, project_id=project_id, granted_by_id=granted_by_id)
        self.db.add(assignment)
        self.db.flush()
        return assignment

    def revoke(self, assignment: RuntimeRoleAssignment) -> None:
        self.db.delete(assignment)
