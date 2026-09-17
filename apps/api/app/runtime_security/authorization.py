"""AuthorizationService — the single place a RuntimeRole-based
authorization decision is made AND audited, combining:
  1. Project isolation + role check (RBACService, rbac.py).
  2. Data-classification restrictions for external runtimes.
  3. The fixed, non-configurable human-approval requirement for the four
     sensitive action kinds this phase names explicitly.

Every call to authorize_action/authorize_sensitive_action writes exactly
one AuditLog row via app.services.audit.record_audit_log — "Audit
authorization decisions" is satisfied by construction, not by a caller
remembering to log separately; there is no code path in this service that
returns/raises without first recording the decision.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import DataClassification
from app.models.enums import RuntimeRole, SensitiveActionKind
from app.runtime_security.identity import AuthenticatedActor
from app.runtime_security.rbac import RBACService
from app.services.audit import record_audit_log

# Fixed by this phase's own instructions — not a configurable policy
# table like app/services/permissions.py's STAGE_APPROVE_ROLES, precisely
# because "require human approval for push, PR creation, PR comments and
# infrastructure actions" is stated as an unconditional requirement, not
# a per-stage default subject to product-spec revision.
_ALWAYS_REQUIRES_HUMAN_APPROVAL = frozenset(SensitiveActionKind)

# A RESTRICTED or CONFIDENTIAL project's work must never be handed to a
# runtime this deployment doesn't operate itself — see
# check_data_classification_for_external_runtime. Mirrors
# app/agent_runtime/policies.py's ScopePolicy/ToolPolicy fail-closed
# convention (Phase 01): unlisted here means NOT allowed, not allowed by
# default.
_CLASSIFICATIONS_ALLOWED_ON_EXTERNAL_RUNTIMES = frozenset({DataClassification.PUBLIC, DataClassification.INTERNAL})


class AuthorizationDeniedError(Exception):
    """Raised by every authorize_* method on denial — always AFTER the
    denial has already been recorded to AuditLog (see each method's own
    body: record, then raise, never the reverse)."""


class AuthorizationDecision:
    def __init__(self, *, allowed: bool, actor_id: uuid.UUID, reason: str, requires_human_approval: bool = False):
        self.allowed = allowed
        self.actor_id = actor_id
        self.reason = reason
        self.requires_human_approval = requires_human_approval


class AuthorizationService:
    def __init__(self, db: Session):
        self.db = db
        self.rbac = RBACService(db)

    def authorize_action(
        self, *, actor: AuthenticatedActor, project_id: uuid.UUID, required_role: RuntimeRole, action_name: str,
    ) -> AuthorizationDecision:
        """Project isolation FIRST (see rbac.py's is_project_member
        docstring for why this check precedes, and is reported
        differently from, the specific-role check), then the role itself.
        """
        if not self.rbac.is_project_member(actor.user_id, project_id):
            decision = AuthorizationDecision(allowed=False, actor_id=actor.user_id, reason=f"Actor has no role on project {project_id} at all.")
            self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision)
            raise AuthorizationDeniedError(decision.reason)

        if not self.rbac.has_role(actor.user_id, required_role, project_id=project_id):
            decision = AuthorizationDecision(allowed=False, actor_id=actor.user_id, reason=f"Actor lacks required role {required_role.value} on project {project_id}.")
            self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision)
            raise AuthorizationDeniedError(decision.reason)

        decision = AuthorizationDecision(allowed=True, actor_id=actor.user_id, reason=f"Actor holds {required_role.value} on project {project_id}.")
        self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision)
        return decision

    def authorize_sensitive_action(
        self, *, actor: AuthenticatedActor, project_id: uuid.UUID, action_kind: SensitiveActionKind, human_approved_by: uuid.UUID | None,
    ) -> AuthorizationDecision:
        """`human_approved_by` must be a real, distinct user id who
        actually recorded approval (a caller's own accept/approve
        endpoint — see e.g. app/api/routes/implementation_runs.py's
        review_implementation_run, Phase 00 baseline section 7 — is
        expected to have already collected this before ever calling
        here). None always denies — there is no "approval optional for
        this action kind" branch, by design (see module-level
        _ALWAYS_REQUIRES_HUMAN_APPROVAL). SELF-APPROVAL IS ALSO DENIED —
        see the `human_approved_by == actor.user_id` check below."""
        action_name = f"sensitive_action.{action_kind.value.lower()}"

        if action_kind not in _ALWAYS_REQUIRES_HUMAN_APPROVAL:
            # Unreachable today (the set is exactly SensitiveActionKind's
            # own members) — kept as an explicit, audited denial rather
            # than an assertion, so a FUTURE action kind added to the enum
            # without also being added to the approval-required set fails
            # closed instead of silently allowing it.
            decision = AuthorizationDecision(allowed=False, actor_id=actor.user_id, reason=f"{action_kind.value} is not recognized as an approval-gated action.")
            self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision)
            raise AuthorizationDeniedError(decision.reason)

        if human_approved_by is None:
            decision = AuthorizationDecision(allowed=False, actor_id=actor.user_id, reason=f"{action_kind.value} requires human approval; none was provided.", requires_human_approval=True)
            self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision)
            raise AuthorizationDeniedError(decision.reason)

        if human_approved_by == actor.user_id:
            # Segregation of duties: the actor requesting/performing a
            # sensitive action can never be the same person who approved
            # it, no matter how privileged their role is — self-approval
            # is exactly as denied as a missing approval.
            decision = AuthorizationDecision(allowed=False, actor_id=actor.user_id, reason=f"{action_kind.value} cannot be self-approved — the approver must be a different person than the actor.", requires_human_approval=True)
            self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision)
            raise AuthorizationDeniedError(decision.reason)

        decision = AuthorizationDecision(allowed=True, actor_id=actor.user_id, reason=f"{action_kind.value} approved by {human_approved_by}.", requires_human_approval=True)
        self._audit(actor=actor, project_id=project_id, action_name=action_name, decision=decision, extra={"approved_by": str(human_approved_by)})
        return decision

    def check_data_classification_for_external_runtime(self, *, data_classification: DataClassification, runtime_name: str) -> None:
        """Raises if `data_classification` is not one this deployment
        permits handing to an external (non-company-operated) runtime —
        see module-level _CLASSIFICATIONS_ALLOWED_ON_EXTERNAL_RUNTIMES.
        Not itself scoped to one actor/project — this is a data-handling
        rule, not a role check — so no AuditLog project_id is implied;
        callers with a project in scope should still log their own
        higher-level audit entry."""
        if data_classification not in _CLASSIFICATIONS_ALLOWED_ON_EXTERNAL_RUNTIMES:
            raise AuthorizationDeniedError(
                f"Data classification {data_classification.value} may not be sent to external runtime '{runtime_name}' — "
                f"only {sorted(c.value for c in _CLASSIFICATIONS_ALLOWED_ON_EXTERNAL_RUNTIMES)} are permitted off company-managed infrastructure."
            )

    def _audit(self, *, actor: AuthenticatedActor, project_id: uuid.UUID | None, action_name: str, decision: AuthorizationDecision, extra: dict | None = None) -> None:
        record_audit_log(
            self.db, project_id=project_id, actor_user_id=actor.user_id, action=f"authorization.{action_name}",
            entity_type="AuthorizationDecision", entity_id=None,
            extra_data={
                "allowed": decision.allowed, "reason": decision.reason, "auth_method": actor.auth_method.value,
                "requires_human_approval": decision.requires_human_approval, **(extra or {}),
            },
        )
