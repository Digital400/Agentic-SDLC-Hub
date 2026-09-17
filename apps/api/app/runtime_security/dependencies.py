"""get_current_actor — the FastAPI dependency that derives an
AuthenticatedActor SERVER-SIDE from the request's own credential (a
verified Authorization header, or the gated local-dev header), never from
a request body field.

ADOPTION STATUS: no existing route in this codebase depends on this yet —
see app/runtime_security/__init__.py's module docstring for why a
wholesale retrofit of every route's existing actor-id-in-body pattern is
out of this phase's scope. This dependency exists, is fully implemented
and tested, and is the mechanism any route — new or existing — adopts to
require Settings.REQUIRE_SERVER_SIDE_IDENTITY-gated server-derived
identity instead of a trusted body field.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.runtime_security.identity import AuthenticatedActor, AuthenticationError
from app.runtime_security.local_dev_provider import LocalDevIdentityProvider
from app.runtime_security.oidc_provider import OIDCIdentityProvider


def _build_provider(settings: Settings) -> OIDCIdentityProvider | LocalDevIdentityProvider:
    if settings.ENTRA_TENANT_ID and settings.ENTRA_CLIENT_ID:
        from app.runtime_security.entra_id import build_entra_id_provider

        return build_entra_id_provider(tenant_id=settings.ENTRA_TENANT_ID, client_id=settings.ENTRA_CLIENT_ID)
    if settings.OIDC_ISSUER and settings.OIDC_AUDIENCE:
        return OIDCIdentityProvider(issuer=settings.OIDC_ISSUER, audience=settings.OIDC_AUDIENCE, jwks_uri=settings.OIDC_JWKS_URI)
    if settings.ENVIRONMENT == "local" and settings.ALLOW_LOCAL_DEV_AUTH:
        return LocalDevIdentityProvider(settings)
    raise AuthenticationError(
        "No identity provider is configured — set OIDC_ISSUER+OIDC_AUDIENCE, ENTRA_TENANT_ID+ENTRA_CLIENT_ID, "
        "or (local development only) ENVIRONMENT=local with ALLOW_LOCAL_DEV_AUTH=True."
    )


def get_current_actor(
    authorization: str | None = Header(None),
    x_dev_user_id: str | None = Header(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedActor:
    """A route adopts server-derived identity simply by adding
    `actor: AuthenticatedActor = Depends(get_current_actor)` to its
    signature — FastAPI resolves the header/db/settings plumbing above
    automatically; nothing route-specific is required beyond that one
    parameter."""
    try:
        provider = _build_provider(settings)
        if isinstance(provider, LocalDevIdentityProvider):
            return provider.authenticate_dev_header(dev_user_id_header=x_dev_user_id, db=db)
        return provider.authenticate(authorization_header=authorization, db=db)
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
