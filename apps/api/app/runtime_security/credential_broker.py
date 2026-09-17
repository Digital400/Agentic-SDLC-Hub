"""RuntimeCredentialBroker — the ONE place a company-managed runtime
obtains a GitHub credential. Prefers a short-lived GitHub App
installation token (expires in ~1 hour, scoped to exactly the
installation's own granted repos/permissions); falls back to the
existing long-lived, encrypted PAT (app/services/github_integration.py,
Phase 00 baseline) only when no GitHub App is configured for this
deployment.

BOUNDARIES THIS MODULE ENFORCES BY CONSTRUCTION, NOT BY CONVENTION:

  - "Company-managed runtimes use approved organization credentials
    only" — every credential this broker issues comes from
    Settings.GITHUB_APP_* (an organization-level App registration) or
    IntegrationConnection (an organization-level connected PAT, Phase 00
    baseline) — there is no code path here that accepts or looks up a
    per-developer credential of any kind.
  - "Developer-local credentials remain on the developer's machine" —
    trivially true by omission: this module has no input, output, or
    dependency that could reach a developer's own local git config, SSH
    keys, or shell environment; it only ever talks to GitHub's own App
    installation-token endpoint and this application's own database.
  - "Never include credentials in WorkPacket or model context" — a
    BrokeredCredential is returned to a CALLER (e.g.
    app/services/github_integration.py's own functions) for one
    outbound API call; nothing in this module ever writes a token into
    an app.agent_runtime.WorkPacket or any compiled prompt (see
    app/prompt_compiler/compiler.py, which never imports this module at
    all) — see
    tests/test_runtime_security_no_credentials_in_context.py for the
    explicit check.

Every issuance is audited (kind + expiry, NEVER the token value) — see
`_audit_issuance`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import decrypt_secret
from app.models import CredentialKind, Repository
from app.services.audit import record_audit_log

_INSTALLATION_TOKEN_URL_TEMPLATE = "https://api.github.com/app/installations/{installation_id}/access_tokens"
_APP_JWT_LIFETIME_SECONDS = 540  # GitHub caps this at 10 minutes; stay comfortably under it
_REQUEST_TIMEOUT_SECONDS = 10.0


class CredentialBrokerError(Exception):
    pass


@dataclass(frozen=True)
class BrokeredCredential:
    """Never logged, never serialized into an audit entry, never placed
    on a WorkPacket — see module docstring. `expires_at` lets a caller
    decide whether to re-broker before a long-running operation outlives
    the token, rather than discovering that mid-call as a 401."""

    token: str
    kind: CredentialKind
    expires_at: datetime | None  # None for the PAT fallback — see app/services/github_integration.py's own docstring: this codebase doesn't track PAT expiry


class RuntimeCredentialBroker:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def get_github_credential(self, repository: Repository, *, requested_by: uuid.UUID | None = None) -> BrokeredCredential:
        if self._github_app_configured():
            try:
                credential = self._issue_github_app_installation_token()
            except CredentialBrokerError:
                credential = self._fallback_to_pat(repository)
        else:
            credential = self._fallback_to_pat(repository)

        self._audit_issuance(repository=repository, credential=credential, requested_by=requested_by)
        return credential

    def _github_app_configured(self) -> bool:
        return bool(self.settings.GITHUB_APP_ID and self.settings.GITHUB_APP_PRIVATE_KEY and self.settings.GITHUB_APP_INSTALLATION_ID)

    def _issue_github_app_installation_token(self) -> BrokeredCredential:
        app_jwt = self._sign_app_jwt()
        url = _INSTALLATION_TOKEN_URL_TEMPLATE.format(installation_id=self.settings.GITHUB_APP_INSTALLATION_ID)
        try:
            response = httpx.post(
                url, headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"}, timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Never includes the App JWT/private key in the message.
            raise CredentialBrokerError(f"GitHub App installation-token exchange failed: {exc}") from exc

        data = response.json()
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        return BrokeredCredential(token=data["token"], kind=CredentialKind.GITHUB_APP_INSTALLATION_TOKEN, expires_at=expires_at)

    def _sign_app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + _APP_JWT_LIFETIME_SECONDS, "iss": self.settings.GITHUB_APP_ID}
        try:
            return jwt.encode(payload, self.settings.GITHUB_APP_PRIVATE_KEY, algorithm="RS256")
        except (ValueError, jwt.InvalidKeyError) as exc:
            raise CredentialBrokerError(f"GITHUB_APP_PRIVATE_KEY is not a valid RSA private key: {exc}") from exc

    def _fallback_to_pat(self, repository: Repository) -> BrokeredCredential:
        if repository.connection is None:
            raise CredentialBrokerError(f"Repository {repository.id} has no connected GitHub credential (no App configured, no PAT connection).")
        token = decrypt_secret(repository.connection.access_token_encrypted)
        return BrokeredCredential(token=token, kind=CredentialKind.GITHUB_PAT_FALLBACK, expires_at=None)

    def _audit_issuance(self, *, repository: Repository, credential: BrokeredCredential, requested_by: uuid.UUID | None) -> None:
        record_audit_log(
            self.db, project_id=repository.project_id, actor_user_id=requested_by, action="credential_broker.issued",
            entity_type="Repository", entity_id=repository.id,
            extra_data={
                "credential_kind": credential.kind.value,
                "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
                # SECURITY: never the token value itself.
            },
        )
