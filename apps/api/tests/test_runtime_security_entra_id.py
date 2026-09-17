"""Tests for app/runtime_security/entra_id.py."""

import pytest

from app.runtime_security.entra_id import build_entra_id_provider
from app.runtime_security.oidc_provider import OIDCConfigurationError


def test_builds_the_correct_v2_issuer_and_jwks_urls():
    provider = build_entra_id_provider(tenant_id="11111111-1111-1111-1111-111111111111", client_id="my-app-id")
    assert provider._issuer == "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0"
    assert provider._jwks_uri == "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/discovery/v2.0/keys"
    assert provider._audience == "my-app-id"


def test_requires_both_tenant_id_and_client_id():
    with pytest.raises(OIDCConfigurationError):
        build_entra_id_provider(tenant_id="", client_id="x")
    with pytest.raises(OIDCConfigurationError):
        build_entra_id_provider(tenant_id="x", client_id="")


def test_is_a_real_oidc_identity_provider_instance():
    from app.runtime_security.oidc_provider import OIDCIdentityProvider

    provider = build_entra_id_provider(tenant_id="tenant", client_id="client")
    assert isinstance(provider, OIDCIdentityProvider)
