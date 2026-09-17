"""Tests for app/runtime_security/local_dev_provider.py — the two-flag
gate, and resolution to a real platform user only."""

import uuid
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models import User, UserRole
from app.runtime_security.identity import AuthenticationError
from app.runtime_security.local_dev_provider import LocalDevIdentityProvider


def _settings(**overrides) -> SimpleNamespace:
    base = Settings().model_dump()
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_user(db, is_active=True) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER, is_active=is_active)
    db.add(user)
    db.flush()
    return user


# --- The two-flag construction gate ------------------------------------------------------


def test_refuses_to_construct_outside_local_environment():
    with pytest.raises(AuthenticationError, match="ENVIRONMENT == 'local'"):
        LocalDevIdentityProvider(_settings(ENVIRONMENT="production", ALLOW_LOCAL_DEV_AUTH=True))


def test_refuses_to_construct_without_the_explicit_allow_flag_even_in_local_env():
    with pytest.raises(AuthenticationError, match="ALLOW_LOCAL_DEV_AUTH"):
        LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=False))


def test_constructs_only_when_both_gates_are_satisfied():
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))
    assert provider.name == "local_dev"


def test_staging_environment_is_also_refused():
    """Only the literal string "local" passes — not "staging", not
    "development", not any other near-miss."""
    with pytest.raises(AuthenticationError):
        LocalDevIdentityProvider(_settings(ENVIRONMENT="staging", ALLOW_LOCAL_DEV_AUTH=True))


# --- authenticate() is not the entry point for this provider -----------------------------


def test_generic_authenticate_method_is_not_supported():
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))
    with pytest.raises(NotImplementedError):
        provider.authenticate(authorization_header="Bearer x", db=None)


# --- authenticate_dev_header ---------------------------------------------------------------


def test_resolves_a_real_existing_user(db):
    user = _make_user(db)
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))

    actor = provider.authenticate_dev_header(dev_user_id_header=str(user.id), db=db)
    assert actor.user_id == user.id
    assert actor.auth_method.value == "LOCAL_DEV"


def test_missing_header_is_rejected(db):
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))
    with pytest.raises(AuthenticationError, match="Missing"):
        provider.authenticate_dev_header(dev_user_id_header=None, db=db)


def test_malformed_uuid_is_rejected(db):
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))
    with pytest.raises(AuthenticationError, match="not a valid UUID"):
        provider.authenticate_dev_header(dev_user_id_header="not-a-uuid", db=db)


def test_nonexistent_user_id_is_rejected(db):
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))
    with pytest.raises(AuthenticationError, match="does not match"):
        provider.authenticate_dev_header(dev_user_id_header=str(uuid.uuid4()), db=db)


def test_deactivated_user_is_rejected(db):
    user = _make_user(db, is_active=False)
    provider = LocalDevIdentityProvider(_settings(ENVIRONMENT="local", ALLOW_LOCAL_DEV_AUTH=True))
    with pytest.raises(AuthenticationError, match="deactivated"):
        provider.authenticate_dev_header(dev_user_id_header=str(user.id), db=db)
