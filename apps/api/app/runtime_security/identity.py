"""AuthenticatedActor — identity derived SERVER-SIDE from a verified
credential, never from a client-supplied field. See
app/runtime_security/__init__.py's module docstring for this phase's
scope and how it relates to the existing (unauthenticated,
actor-id-in-request-body) pattern documented in
docs/architecture/universal-agent-runtime-baseline.md section 10.

HARD RULE this whole package exists to make structural: nothing in this
module, or anything that constructs an AuthenticatedActor, ever accepts a
raw user id from request JSON/query params/path params as its SOURCE of
identity — only from a verified token (OIDC) or an explicitly
environment-gated local-dev mechanism (see local_dev_provider.py). A
route's OWN business-logic id fields (e.g. `reviewer_id` naming who a
review is assigned TO) are a different, legitimate concept and are
untouched by this rule — see the package docstring's "what this phase
does and does not change" section.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.enums import AuthenticationMethod

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class AuthenticationError(Exception):
    """Raised when a request's credential is missing, malformed, expired,
    or fails signature/issuer/audience verification. A route surfaces
    this as 401, never as a 400 (a 400 would imply the caller can just
    "fix their input" — an authentication failure is not a request-shape
    problem)."""


@dataclass(frozen=True)
class AuthenticatedActor:
    """The one, verified representation of "who is making this request" —
    every authorization decision in this package (see authorization.py)
    is a function of this object, never of a request body field.
    """

    user_id: uuid.UUID
    email: str
    auth_method: AuthenticationMethod
    # Raw claims from the verified token (OIDC) or the local-dev
    # provider's own fixed shape — kept for audit/debugging, never
    # trusted directly for an authorization decision (RBACService always
    # re-resolves roles from RuntimeRoleAssignment rows, never from a
    # claims dict a caller could otherwise shape).
    raw_claims: dict = field(default_factory=dict)


class IdentityProvider(abc.ABC):
    """One way to turn an inbound HTTP request's credential into an
    AuthenticatedActor. See oidc_provider.py (OIDCIdentityProvider) and
    local_dev_provider.py (LocalDevIdentityProvider) for the two concrete
    implementations this phase adds."""

    name: str

    @abc.abstractmethod
    def authenticate(self, *, authorization_header: str | None, db: "Session") -> AuthenticatedActor:
        """Raises AuthenticationError on any failure — never returns a
        partially-populated or "guessed" actor. `db` is required because
        resolving a verified external identity (an OIDC `sub`/`email`
        claim) down to a real platform `User.id` — the only thing
        RBACService/AuthorizationService ever key off of — needs a
        lookup; no implementation is expected to trust a user id claimed
        directly inside the token as a platform id without that lookup."""
