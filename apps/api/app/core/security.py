"""Secret encryption for third-party credentials stored at rest — currently
only the GitHub PAT on `IntegrationConnection.access_token_encrypted` (see
app/models/integration_connection.py).

`docs/architecture.md`'s MCP integrations section documents the intended
end state as "credentials in a vault... never persisted in this table or
logged." No vault exists anywhere in this codebase yet, so this module is
a deliberate, disclosed bridge: the token is encrypted at rest with a
symmetric key (Fernet — AES-128-CBC + HMAC, from the `cryptography`
package) rather than stored in plaintext, but it is still an app-managed
secret in Postgres, not a vault-resolved one. Replace this module with a
real vault client when one exists; do not extend it to hold more secret
types without revisiting that decision.

Nothing in this module ever logs a plaintext secret — callers must not
either (see GitHubIntegrationService's own docstring and every route that
touches a token).
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretDecryptionError(Exception):
    """The stored ciphertext couldn't be decrypted — a tampered/corrupted
    value, or (most commonly in practice) GITHUB_TOKEN_ENCRYPTION_KEY
    changed since this secret was encrypted. Fails closed: callers must
    treat this the same as "no valid token," never fall back to a default."""


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.GITHUB_TOKEN_ENCRYPTION_KEY:
        key = settings.GITHUB_TOKEN_ENCRYPTION_KEY.encode()
    else:
        # Dev-only, deterministic fallback so the app stays runnable without
        # extra setup — NOT safe for any real secret. A fixed, literal
        # Fernet key would be just as bad (checked into source); deriving it
        # from a fixed string is equivalent in effect but makes the "this is
        # not a real secret" property obvious at the call site instead of
        # looking like a real key.
        key = base64.urlsafe_b64encode(hashlib.sha256(b"agentic-sdlc-hub-dev-only-insecure-key").digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptionError("Stored secret could not be decrypted.") from exc


def mask_secret(plaintext: str) -> str:
    """Display-only — the real value is never returned by any API response;
    this is what a UI shows instead (e.g. "****d3f9")."""
    if len(plaintext) <= 4:
        return "*" * len(plaintext)
    return f"****{plaintext[-4:]}"


def last_four(plaintext: str) -> str:
    """Just the trailing characters, for storing alongside the ciphertext
    as `IntegrationConnection.token_last_four` — cheaper than re-decrypting
    just to render a masked hint."""
    return plaintext[-4:] if len(plaintext) >= 4 else plaintext
