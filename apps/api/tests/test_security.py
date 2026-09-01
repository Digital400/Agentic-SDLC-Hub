"""Unit tests for app/core/security.py — the GitHub PAT encryption-at-rest
helper (a disclosed, pragmatic bridge; see that module's own docstring for
why this isn't the vault-based design docs/architecture.md calls for).
"""

import pytest

from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret, last_four, mask_secret


def test_encrypt_decrypt_round_trip():
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"

    ciphertext = encrypt_secret(token)

    assert ciphertext != token
    assert decrypt_secret(ciphertext) == token


def test_ciphertext_does_not_contain_the_plaintext():
    token = "ghp_supersecrettoken1234567890"

    ciphertext = encrypt_secret(token)

    assert token not in ciphertext


def test_decrypting_garbage_fails_closed():
    with pytest.raises(SecretDecryptionError):
        decrypt_secret("this-is-not-a-valid-fernet-token")


def test_decrypting_a_tampered_ciphertext_fails_closed():
    ciphertext = encrypt_secret("ghp_realtoken1234")
    tampered = ciphertext[:-4] + ("A" if ciphertext[-4] != "A" else "B") + ciphertext[-3:]

    with pytest.raises(SecretDecryptionError):
        decrypt_secret(tampered)


def test_mask_secret_reveals_only_the_last_four_characters():
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"

    masked = mask_secret(token)

    assert masked == "****6789"
    assert masked != token
    assert token[:-4] not in masked


def test_mask_secret_handles_short_input_without_leaking_it():
    assert mask_secret("ab") == "**"


def test_last_four_matches_what_mask_secret_shows():
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"

    assert mask_secret(token) == f"****{last_four(token)}"
