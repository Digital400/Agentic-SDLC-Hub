"""Runtime security and credential broker — Phase 07.

FINDING THIS PHASE STARTS FROM (see
docs/architecture/runtime-security-baseline.md for the full inspection):
this codebase has NO authentication layer. Every mutating endpoint trusts
an explicit actor id present in the request body/query (`triggered_by_
user_id`, `created_by_id`, `reviewer_id`, ...) — documented as a known,
deliberate MVP gap in app/services/permissions.py's own module docstring,
and confirmed in the Phase 00 architecture baseline (section 10). A
second, related finding: app/models/project.py's `ProjectMember`/
`ProjectRole` pair is written once at project creation and never read or
enforced anywhere — project-level RBAC has always been schema-only, not
real.

WHAT THIS PHASE ADDS (all additive — see each module below):
  - identity.py / oidc_provider.py / entra_id.py / local_dev_provider.py /
    dependencies.py — real, verified, server-side identity derivation:
    OIDC (any standards-compliant issuer, including Microsoft Entra ID)
    for a real deployment, a narrowly double-gated local-dev adapter for
    development. get_current_actor is the FastAPI dependency a route
    adopts to require this instead of trusting a body field.
  - rbac.py — RuntimeRole (8 roles) + RuntimeRoleAssignment (a NEW,
    actually-enforced org/project-scoped grant table) + RBACService,
    including real project isolation (see rbac.py's own docstring).
  - authorization.py — AuthorizationService: role check + project
    isolation + the fixed, unconditional human-approval requirement for
    push/PR-create/PR-comment/infrastructure actions + data-classification
    restriction for external runtimes — every decision audited.
  - credential_broker.py / secret_provider.py — RuntimeCredentialBroker
    (short-lived GitHub App installation tokens, preferred over the
    existing long-lived PAT) and the SecretProvider abstraction it
    resolves its own configuration secrets through.
  - security_gate.py — the master switch keeping Phase 06's
    CeleryJobDispatcher (the only "external coding runtime" this codebase
    has) refused-at-construction-time until this gate passes.

WHAT THIS PHASE DELIBERATELY DOES NOT DO: retrofit get_current_actor (or
RBACService/AuthorizationService) onto the dozens of pre-existing routes
that still trust a body-supplied actor id. That existing pattern is
UNCHANGED and fully preserved — every route from Phases 00-06 behaves
exactly as it did before this phase, and every one of their existing
tests passes unmodified. Doing a wholesale authentication retrofit across
the whole API is explicitly a LATER migration this phase's own
instructions ("implement only the requested phase") do not authorize;
this phase delivers the complete, real, tested infrastructure that
retrofit would be built on, and wires it into the one place this
project's own instructions single out by name: the external-coding-
runtime gate (Settings.AGENT_JOB_DISPATCHER_MODE == "celery").
"""

from app.runtime_security.authorization import AuthorizationDecision, AuthorizationDeniedError, AuthorizationService
from app.runtime_security.credential_broker import BrokeredCredential, CredentialBrokerError, RuntimeCredentialBroker
from app.runtime_security.dependencies import get_current_actor
from app.runtime_security.identity import AuthenticatedActor, AuthenticationError, IdentityProvider
from app.runtime_security.rbac import RBACService
from app.runtime_security.secret_provider import EnvSecretProvider, SecretNotFoundError, SecretProvider
from app.runtime_security.security_gate import SecurityGateNotPassedError, SecurityGateService, SecurityGateStatus, require_security_gate_passed

__all__ = [
    "AuthorizationDecision",
    "AuthorizationDeniedError",
    "AuthorizationService",
    "BrokeredCredential",
    "CredentialBrokerError",
    "RuntimeCredentialBroker",
    "get_current_actor",
    "AuthenticatedActor",
    "AuthenticationError",
    "IdentityProvider",
    "RBACService",
    "EnvSecretProvider",
    "SecretNotFoundError",
    "SecretProvider",
    "SecurityGateNotPassedError",
    "SecurityGateService",
    "SecurityGateStatus",
    "require_security_gate_passed",
]
