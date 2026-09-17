"""LocalDevIdentityProvider — the "Local-development identity adapter"
this phase requires: a deliberate, narrowly-gated carve-out so a
developer can exercise an authenticated code path without standing up a
real OIDC IdP.

THIS IS NOT AN EXCEPTION TO "never trust a browser-supplied actor id" —
it is a documented, structurally-gated DEVELOPMENT-ONLY mechanism:

  1. Settings.ENVIRONMENT must literally equal "local" — never "staging"/
     "production"/anything else, checked at CONSTRUCTION time, not
     per-request (see __init__ below).
  2. Settings.ALLOW_LOCAL_DEV_AUTH must ALSO be explicitly True — two
     independent flags, both required, so a single misconfigured value
     can't silently open this path in a real deployment.
  3. Even then, this only resolves an id the caller supplies via the
     `X-Dev-User-Id` header to a REAL, EXISTING, ACTIVE platform `User`
     row — it never fabricates a user, and the resolved actor is still
     recorded with AuthenticationMethod.LOCAL_DEV on every audit entry
     (see authorization.py), so a local-dev-authenticated action is
     always distinguishable from a real corporate sign-in in the audit
     trail.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import User
from app.models.enums import AuthenticationMethod
from app.runtime_security.identity import AuthenticatedActor, AuthenticationError, IdentityProvider


class LocalDevIdentityProvider(IdentityProvider):
    name = "local_dev"

    def __init__(self, settings: Settings):
        if settings.ENVIRONMENT != "local":
            raise AuthenticationError("LocalDevIdentityProvider may only be constructed when Settings.ENVIRONMENT == 'local'.")
        if not settings.ALLOW_LOCAL_DEV_AUTH:
            raise AuthenticationError("LocalDevIdentityProvider requires Settings.ALLOW_LOCAL_DEV_AUTH=True — both gates must be explicit.")

    def authenticate(self, *, authorization_header: str | None, db: Session) -> AuthenticatedActor:
        raise NotImplementedError("Use authenticate_dev_header — this provider is header-driven, not Bearer-token-driven; see that method.")

    def authenticate_dev_header(self, *, dev_user_id_header: str | None, db: Session) -> AuthenticatedActor:
        if not dev_user_id_header:
            raise AuthenticationError("Missing X-Dev-User-Id header.")
        try:
            user_id = uuid.UUID(dev_user_id_header)
        except ValueError as exc:
            raise AuthenticationError(f"X-Dev-User-Id '{dev_user_id_header}' is not a valid UUID.") from exc

        user = db.get(User, user_id)
        if user is None:
            raise AuthenticationError(f"X-Dev-User-Id '{user_id}' does not match an existing user.")
        if not user.is_active:
            raise AuthenticationError(f"User '{user.email}' is deactivated.")

        return AuthenticatedActor(user_id=user.id, email=user.email, auth_method=AuthenticationMethod.LOCAL_DEV, raw_claims={"dev_user_id_header": dev_user_id_header})
