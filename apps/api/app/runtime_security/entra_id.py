"""Corporate Microsoft Entra ID configuration — a thin preset over
OIDCIdentityProvider (see oidc_provider.py's own module docstring: Entra
ID issues standard OIDC/JWT tokens, so no separate token-validation
codepath is needed, only Entra's own issuer/JWKS URL shape).

https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc
documents the v2.0 issuer/JWKS URL shape this module builds.
"""

from __future__ import annotations

from app.runtime_security.oidc_provider import OIDCConfigurationError, OIDCIdentityProvider


def build_entra_id_provider(*, tenant_id: str, client_id: str) -> OIDCIdentityProvider:
    """`tenant_id` is the Entra ID (Azure AD) tenant GUID or verified
    domain; `client_id` is this application's own registered App ID —
    used as the expected `aud` claim, so a token issued for a DIFFERENT
    application in the same tenant is correctly rejected."""
    if not tenant_id or not client_id:
        raise OIDCConfigurationError("build_entra_id_provider requires both tenant_id and client_id.")
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    return OIDCIdentityProvider(issuer=issuer, audience=client_id, jwks_uri=jwks_uri)
