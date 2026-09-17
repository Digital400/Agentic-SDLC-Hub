"""Tests for app/runtime_security/oidc_provider.py — real JWT signing and
verification against a locally-generated RSA keypair (never a real
network call to a real IdP; JWKS fetches are monkeypatched).
"""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.models import User, UserRole
from app.runtime_security.identity import AuthenticationError
from app.runtime_security.oidc_provider import OIDCConfigurationError, OIDCIdentityProvider

ISSUER = "https://issuer.example.com"
AUDIENCE = "test-client-id"
KID = "test-key-1"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


def _jwk_from_public_key(public_key, kid: str) -> dict:
    numbers = public_key.public_numbers()

    def _b64(n: int) -> str:
        import base64

        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": _b64(numbers.n), "e": _b64(numbers.e)}


def _sign_token(private_key, *, claims: dict, kid: str = KID) -> str:
    pem = private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption())
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def _default_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 3600, "email": "actor@example.com"}
    claims.update(overrides)
    return claims


def _jwks_response(public_key) -> httpx.Response:
    response = httpx.Response(200, json={"keys": [_jwk_from_public_key(public_key, KID)]})
    response.request = httpx.Request("GET", "https://issuer.example.com/.well-known/jwks.json")
    return response


def _install_jwks(monkeypatch, provider, public_key):
    def _fake_get(url, timeout):
        return _jwks_response(public_key)

    monkeypatch.setattr(httpx, "get", _fake_get)


def _make_user(db, email="actor@example.com", is_active=True) -> User:
    user = User(email=email, full_name="Actor", role=UserRole.DEVELOPER, is_active=is_active)
    db.add(user)
    db.flush()
    return user


# --- Construction ------------------------------------------------------------------------


def test_construction_requires_issuer_and_audience():
    with pytest.raises(OIDCConfigurationError):
        OIDCIdentityProvider(issuer="", audience="x")
    with pytest.raises(OIDCConfigurationError):
        OIDCIdentityProvider(issuer="https://x.example.com", audience="")


def test_default_jwks_uri_is_derived_from_issuer():
    provider = OIDCIdentityProvider(issuer="https://x.example.com/", audience="aud")
    assert provider._jwks_uri == "https://x.example.com/.well-known/jwks.json"


# --- Authentication (happy path) ----------------------------------------------------------


def test_valid_token_resolves_to_the_matching_platform_user(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    _make_user(db, email="actor@example.com")
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    token = _sign_token(private_key, claims=_default_claims())
    actor = provider.authenticate(authorization_header=f"Bearer {token}", db=db)

    assert actor.email == "actor@example.com"
    assert actor.auth_method.value == "OIDC"


# --- Failure modes -------------------------------------------------------------------------


def test_missing_authorization_header_raises(db):
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    with pytest.raises(AuthenticationError, match="Missing"):
        provider.authenticate(authorization_header=None, db=db)


def test_non_bearer_scheme_raises(db):
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    with pytest.raises(AuthenticationError, match="Bearer"):
        provider.authenticate(authorization_header="Basic abc123", db=db)


def test_expired_token_is_rejected(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    expired_claims = _default_claims(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    token = _sign_token(private_key, claims=expired_claims)

    with pytest.raises(AuthenticationError, match="expired"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_wrong_audience_is_rejected(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    token = _sign_token(private_key, claims=_default_claims(aud="a-different-client-id"))
    with pytest.raises(AuthenticationError, match="verification"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_wrong_issuer_is_rejected(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    token = _sign_token(private_key, claims=_default_claims(iss="https://a-different-issuer.example.com"))
    with pytest.raises(AuthenticationError, match="verification"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_token_signed_by_a_different_key_is_rejected(db, monkeypatch):
    """The critical forgery-resistance check: a token signed with a KEY
    NOT in the issuer's own published JWKS must never verify, even if
    every claim (iss/aud/exp) is otherwise perfectly formed."""
    real_public_key = None
    attacker_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    legit_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    real_public_key = legit_private_key.public_key()

    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, real_public_key)

    forged_token = _sign_token(attacker_private_key, claims=_default_claims())
    with pytest.raises(AuthenticationError, match="verification"):
        provider.authenticate(authorization_header=f"Bearer {forged_token}", db=db)


def test_malformed_token_is_rejected(db):
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    with pytest.raises(AuthenticationError, match="Malformed"):
        provider.authenticate(authorization_header="Bearer not-a-real-jwt", db=db)


def test_unknown_kid_is_rejected(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    token = _sign_token(private_key, claims=_default_claims(), kid="a-kid-not-in-the-jwks")
    with pytest.raises(AuthenticationError, match="No matching JWKS"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_verified_token_for_a_nonexistent_platform_user_is_rejected(db, rsa_keypair, monkeypatch):
    """Cryptographic verification succeeding is NOT enough — the claimed
    identity must resolve to a real, provisioned platform user."""
    private_key, public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    token = _sign_token(private_key, claims=_default_claims(email="nobody-provisioned@example.com"))
    with pytest.raises(AuthenticationError, match="no platform user"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_deactivated_user_is_rejected(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    _make_user(db, email="deactivated@example.com", is_active=False)
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    token = _sign_token(private_key, claims=_default_claims(email="deactivated@example.com"))
    with pytest.raises(AuthenticationError, match="deactivated"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_missing_email_claim_is_rejected(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    _install_jwks(monkeypatch, provider, public_key)

    claims = _default_claims()
    del claims["email"]
    token = _sign_token(private_key, claims=claims)
    with pytest.raises(AuthenticationError, match="email"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


def test_jwks_fetch_failure_is_a_clean_authentication_error(db, rsa_keypair, monkeypatch):
    """A syntactically valid, well-formed JWT (so header-parsing succeeds
    and the code actually reaches the JWKS fetch) whose signing key the
    (failing) JWKS endpoint can never be consulted for."""
    private_key, _public_key = rsa_keypair
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)
    token = _sign_token(private_key, claims=_default_claims())

    def _raise(*a, **k):
        raise httpx.ConnectTimeout("jwks endpoint unreachable")

    monkeypatch.setattr(httpx, "get", _raise)
    with pytest.raises(AuthenticationError, match="JWKS"):
        provider.authenticate(authorization_header=f"Bearer {token}", db=db)


# --- JWKS caching ---------------------------------------------------------------------------


def test_jwks_is_cached_across_calls(db, rsa_keypair, monkeypatch):
    private_key, public_key = rsa_keypair
    _make_user(db, email="actor@example.com")
    provider = OIDCIdentityProvider(issuer=ISSUER, audience=AUDIENCE)

    calls = []

    def _fake_get(url, timeout):
        calls.append(1)
        return _jwks_response(public_key)

    monkeypatch.setattr(httpx, "get", _fake_get)

    token = _sign_token(private_key, claims=_default_claims())
    provider.authenticate(authorization_header=f"Bearer {token}", db=db)
    provider.authenticate(authorization_header=f"Bearer {token}", db=db)
    assert len(calls) == 1
