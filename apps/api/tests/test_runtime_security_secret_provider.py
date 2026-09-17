"""Tests for app/runtime_security/secret_provider.py."""

import pytest

from app.runtime_security.secret_provider import EnvSecretProvider, SecretNotFoundError


def test_reads_a_set_environment_variable(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_KEY_XYZ", "value123")
    assert EnvSecretProvider().get_secret("TEST_SECRET_KEY_XYZ") == "value123"


def test_raises_for_an_unset_variable(monkeypatch):
    monkeypatch.delenv("TEST_SECRET_KEY_NOT_SET", raising=False)
    with pytest.raises(SecretNotFoundError):
        EnvSecretProvider().get_secret("TEST_SECRET_KEY_NOT_SET")


def test_raises_for_an_empty_string_value_not_returns_empty(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_KEY_EMPTY", "")
    with pytest.raises(SecretNotFoundError):
        EnvSecretProvider().get_secret("TEST_SECRET_KEY_EMPTY")
