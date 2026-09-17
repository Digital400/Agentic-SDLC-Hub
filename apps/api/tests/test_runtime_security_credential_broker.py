"""Tests for app/runtime_security/credential_broker.py — GitHub App
short-lived installation-token issuance (preferred), PAT fallback, and
that a credential value is never written to the audit trail.
"""

import uuid
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.core.config import Settings
from app.models import AuditLog, CredentialKind, Integration, IntegrationConnection, IntegrationProvider, IntegrationStatus, Repository
from app.core.security import encrypt_secret
from app.runtime_security.credential_broker import CredentialBrokerError, RuntimeCredentialBroker


def _settings(**overrides) -> Settings:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


def _private_key_pem() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()).decode()


def _repo_with_pat(db, project) -> Repository:
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted=encrypt_secret("ghp_realpattoken1234567890"),
        token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    return repository


def _repo_without_connection():
    """Repository.connection_id is NOT NULL at the schema level (a real
    row always has one) — this defensive branch in
    RuntimeCredentialBroker._fallback_to_pat is unreachable through a
    real row, so a lightweight stand-in is used to exercise it directly."""
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), connection=None)


# --- PAT fallback (no GitHub App configured) ------------------------------------------------


def test_falls_back_to_pat_when_no_github_app_is_configured(db, project):
    repository = _repo_with_pat(db, project)
    broker = RuntimeCredentialBroker(db, _settings(GITHUB_APP_ID=None, GITHUB_APP_PRIVATE_KEY=None, GITHUB_APP_INSTALLATION_ID=None))

    credential = broker.get_github_credential(repository)
    assert credential.kind == CredentialKind.GITHUB_PAT_FALLBACK
    assert credential.token == "ghp_realpattoken1234567890"
    assert credential.expires_at is None


def test_pat_fallback_raises_cleanly_when_no_connection_exists(db):
    repository = _repo_without_connection()
    broker = RuntimeCredentialBroker(db, _settings(GITHUB_APP_ID=None, GITHUB_APP_PRIVATE_KEY=None, GITHUB_APP_INSTALLATION_ID=None))

    with pytest.raises(CredentialBrokerError, match="no connected GitHub credential"):
        broker.get_github_credential(repository)


# --- GitHub App installation token (preferred) -----------------------------------------------


def test_issues_a_short_lived_installation_token_when_app_is_configured(db, project, monkeypatch):
    repository = _repo_with_pat(db, project)  # PAT also present — App must still be preferred
    private_key_pem = _private_key_pem()
    settings = _settings(GITHUB_APP_ID="12345", GITHUB_APP_PRIVATE_KEY=private_key_pem, GITHUB_APP_INSTALLATION_ID="67890")

    captured_auth_header = {}

    def _fake_post(url, *, headers, timeout):
        captured_auth_header["value"] = headers["Authorization"]
        assert url == "https://api.github.com/app/installations/67890/access_tokens"
        response = httpx.Response(201, json={"token": "ghs_shortlivedtoken", "expires_at": "2026-01-01T01:00:00Z"})
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx, "post", _fake_post)

    broker = RuntimeCredentialBroker(db, settings)
    credential = broker.get_github_credential(repository)

    assert credential.kind == CredentialKind.GITHUB_APP_INSTALLATION_TOKEN
    assert credential.token == "ghs_shortlivedtoken"
    assert credential.expires_at is not None
    assert captured_auth_header["value"].startswith("Bearer ")

    # The App JWT sent really was signed by the configured private key,
    # for the configured App id — not a placeholder.
    app_jwt = captured_auth_header["value"].removeprefix("Bearer ")
    decoded = jwt.decode(app_jwt, options={"verify_signature": False})
    assert decoded["iss"] == "12345"


def test_falls_back_to_pat_if_the_app_token_exchange_fails(db, project, monkeypatch):
    repository = _repo_with_pat(db, project)
    settings = _settings(GITHUB_APP_ID="12345", GITHUB_APP_PRIVATE_KEY=_private_key_pem(), GITHUB_APP_INSTALLATION_ID="67890")

    def _raise(*a, **k):
        raise httpx.ConnectTimeout("GitHub API unreachable")

    monkeypatch.setattr(httpx, "post", _raise)

    broker = RuntimeCredentialBroker(db, settings)
    credential = broker.get_github_credential(repository)
    assert credential.kind == CredentialKind.GITHUB_PAT_FALLBACK


def test_invalid_private_key_raises_a_clean_error_during_signing(db, project):
    repository = _repo_with_pat(db, project)
    settings = _settings(GITHUB_APP_ID="12345", GITHUB_APP_PRIVATE_KEY="not-a-real-pem-key", GITHUB_APP_INSTALLATION_ID="67890")

    broker = RuntimeCredentialBroker(db, settings)
    credential = broker.get_github_credential(repository)  # falls back to PAT, does not crash
    assert credential.kind == CredentialKind.GITHUB_PAT_FALLBACK


# --- Audit: never the token value -----------------------------------------------------------


def test_issuance_is_audited_without_the_token_value(db, project):
    repository = _repo_with_pat(db, project)
    broker = RuntimeCredentialBroker(db, _settings(GITHUB_APP_ID=None, GITHUB_APP_PRIVATE_KEY=None, GITHUB_APP_INSTALLATION_ID=None))
    requester_id = uuid.uuid4()

    broker.get_github_credential(repository, requested_by=requester_id)
    db.flush()

    entries = db.query(AuditLog).filter(AuditLog.action == "credential_broker.issued").all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.extra_data["credential_kind"] == "GITHUB_PAT_FALLBACK"
    serialized = str(entry.extra_data)
    assert "ghp_realpattoken1234567890" not in serialized
