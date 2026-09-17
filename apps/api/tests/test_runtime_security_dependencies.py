"""Tests for app/runtime_security/dependencies.py's get_current_actor —
provider-selection precedence and that every failure surfaces as 401."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.models import User, UserRole
from app.runtime_security.dependencies import _build_provider, get_current_actor
from app.runtime_security.entra_id import build_entra_id_provider
from app.runtime_security.identity import AuthenticationError
from app.runtime_security.local_dev_provider import LocalDevIdentityProvider
from app.runtime_security.oidc_provider import OIDCIdentityProvider


def _settings(**overrides) -> Settings:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


# --- Provider-selection precedence -----------------------------------------------------


def test_entra_id_is_preferred_when_configured():
    settings = _settings(ENTRA_TENANT_ID="tenant", ENTRA_CLIENT_ID="client", OIDC_ISSUER="https://other.example.com", OIDC_AUDIENCE="other-aud")
    provider = _build_provider(settings)
    assert isinstance(provider, OIDCIdentityProvider)
    assert "login.microsoftonline.com" in provider._issuer


def test_generic_oidc_used_when_entra_not_configured():
    settings = _settings(OIDC_ISSUER="https://issuer.example.com", OIDC_AUDIENCE="aud")
    provider = _build_provider(settings)
    assert isinstance(provider, OIDCIdentityProvider)
    assert provider._issuer == "https://issuer.example.com"


def test_local_dev_used_only_as_last_resort():
    settings = _settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True)
    provider = _build_provider(settings)
    assert isinstance(provider, LocalDevIdentityProvider)


def test_no_provider_configured_raises_authentication_error():
    settings = _settings(ENVIRONMENT="production", ALLOW_LOCAL_DEV_AUTH=False)
    with pytest.raises(AuthenticationError, match="No identity provider"):
        _build_provider(settings)


def test_local_dev_not_selected_outside_local_environment_even_if_allowed():
    settings = _settings(ENVIRONMENT="production", ALLOW_LOCAL_DEV_AUTH=True)
    with pytest.raises(AuthenticationError):
        _build_provider(settings)


# --- get_current_actor: end-to-end via the local-dev path (no real IdP needed) ------------


def _make_user(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def test_get_current_actor_resolves_via_local_dev_header(db):
    user = _make_user(db)
    settings = _settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True)

    actor = get_current_actor(authorization=None, x_dev_user_id=str(user.id), db=db, settings=settings)
    assert actor.user_id == user.id


def test_get_current_actor_raises_401_not_a_generic_exception(db):
    settings = _settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True)

    with pytest.raises(HTTPException) as exc_info:
        get_current_actor(authorization=None, x_dev_user_id=None, db=db, settings=settings)
    assert exc_info.value.status_code == 401


def test_get_current_actor_401s_when_no_provider_is_configured(db):
    settings = _settings(ENVIRONMENT="production", ALLOW_LOCAL_DEV_AUTH=False)
    with pytest.raises(HTTPException) as exc_info:
        get_current_actor(authorization="Bearer whatever", x_dev_user_id=None, db=db, settings=settings)
    assert exc_info.value.status_code == 401
