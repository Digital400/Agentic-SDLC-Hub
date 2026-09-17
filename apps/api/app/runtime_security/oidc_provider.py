"""OIDCIdentityProvider — validates a Bearer JWT against a configured
OIDC issuer (signature via the issuer's own published JWKS, `iss`,
`aud`, and expiry), then resolves the verified `email` claim to a real
platform `User` row. This is the "Validated OIDC authentication" this
phase requires — every check below is a genuine cryptographic/claim
verification, not a decoded-and-trusted shortcut.

Works for ANY standards-compliant OIDC issuer, including Microsoft Entra
ID — see entra_id.py, a thin preset that configures this class with
Entra's own issuer URL shape; there is no separate Entra-specific token
validator, because Entra ID's tokens are standard OIDC/JWT.
"""

from __future__ import annotations

import time

import httpx
import jwt
from sqlalchemy.orm import Session

from app.models import User
from app.models.enums import AuthenticationMethod
from app.runtime_security.identity import AuthenticatedActor, AuthenticationError, IdentityProvider

_JWKS_CACHE_TTL_SECONDS = 300.0


class OIDCConfigurationError(Exception):
    """Raised at construction time when required OIDC settings are
    missing — fails fast at startup/first-use rather than on every
    request."""


class OIDCIdentityProvider(IdentityProvider):
    name = "oidc"

    def __init__(self, *, issuer: str, audience: str, jwks_uri: str | None = None):
        if not issuer or not audience:
            raise OIDCConfigurationError("OIDCIdentityProvider requires both issuer and audience.")
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._jwks_uri = jwks_uri or f"{self._issuer}/.well-known/jwks.json"
        self._jwks_cache: dict | None = None
        self._jwks_cached_at: float = 0.0

    def authenticate(self, *, authorization_header: str | None, db: Session) -> AuthenticatedActor:
        token = self._extract_bearer_token(authorization_header)
        claims = self._verify_token(token)

        email = claims.get("email") or claims.get("preferred_username")
        if not email:
            raise AuthenticationError("Verified token carries no 'email' or 'preferred_username' claim to resolve an identity from.")

        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise AuthenticationError(f"Token verified successfully, but no platform user exists for '{email}' — provisioning is out of this provider's scope.")
        if not user.is_active:
            raise AuthenticationError(f"User '{email}' is deactivated.")

        return AuthenticatedActor(user_id=user.id, email=user.email, auth_method=AuthenticationMethod.OIDC, raw_claims=claims)

    @staticmethod
    def _extract_bearer_token(authorization_header: str | None) -> str:
        if not authorization_header:
            raise AuthenticationError("Missing Authorization header.")
        parts = authorization_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            raise AuthenticationError("Authorization header must be 'Bearer <token>'.")
        return parts[1].strip()

    def _verify_token(self, token: str) -> dict:
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Malformed JWT: {exc}") from exc

        kid = unverified_header.get("kid")
        signing_key = self._signing_key_for(kid)

        try:
            claims = jwt.decode(
                token, key=signing_key, algorithms=["RS256"], audience=self._audience, issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            # Never echoes the raw token back in the error — only PyJWT's
            # own diagnostic message (issuer/audience/signature mismatch
            # wording), never a token substring.
            raise AuthenticationError(f"Token failed verification: {exc}") from exc
        return claims

    def _signing_key_for(self, kid: str | None):
        jwks = self._get_jwks()
        keys = jwks.get("keys", [])
        candidates = [k for k in keys if kid is None or k.get("kid") == kid]
        if not candidates:
            raise AuthenticationError(f"No matching JWKS signing key found for kid='{kid}'.")
        return jwt.PyJWK(candidates[0]).key

    def _get_jwks(self) -> dict:
        now = time.monotonic()
        if self._jwks_cache is not None and (now - self._jwks_cached_at) < _JWKS_CACHE_TTL_SECONDS:
            return self._jwks_cache
        try:
            response = httpx.get(self._jwks_uri, timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthenticationError(f"Could not fetch JWKS from '{self._jwks_uri}': {exc}") from exc
        self._jwks_cache = response.json()
        self._jwks_cached_at = now
        return self._jwks_cache
