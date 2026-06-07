"""Round-trip and tamper tests for lib/encryption.py.

API keys are encrypted-at-rest with no plaintext recovery from a database
dump. These tests prove the encryption layer is reversible only with the
correct key.
"""

from __future__ import annotations

import sys

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, "/app/lib")
import encryption  # noqa: E402


def test_round_trip(monkeypatch):
    """Encrypt then decrypt returns the original plaintext byte-for-byte."""
    monkeypatch.setenv("AITG_ENCRYPTION_KEY", Fernet.generate_key().decode())

    plaintext = "sk-proj-aBcDeF1234567890_realistic_openai_key_shape"
    ciphertext = encryption.encrypt(plaintext)
    assert ciphertext != plaintext
    assert encryption.decrypt(ciphertext) == plaintext


def test_decrypt_fails_with_wrong_key(monkeypatch):
    """Rotating to a different key invalidates earlier ciphertexts."""
    monkeypatch.setenv("AITG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = encryption.encrypt("secret")

    # Rotate to a new key. The old ciphertext must no longer decrypt.
    monkeypatch.setenv("AITG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="Decryption failed"):
        encryption.decrypt(ciphertext)


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("AITG_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AITG_ENCRYPTION_KEY is not set"):
        encryption.encrypt("anything")


def test_invalid_key_raises(monkeypatch):
    monkeypatch.setenv("AITG_ENCRYPTION_KEY", "this-is-not-a-valid-fernet-key")
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        encryption.encrypt("anything")


def test_empty_input_raises(monkeypatch):
    monkeypatch.setenv("AITG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        encryption.encrypt("")
    with pytest.raises(ValueError):
        encryption.decrypt("")


def test_db_round_trip(conn, monkeypatch):
    """Insert encrypted, read back, decrypt — proves the DB column can carry
    Fernet output (TEXT type, no length truncation)."""
    monkeypatch.setenv("AITG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    original = "sk-proj-database-round-trip-test"
    ciphertext = encryption.encrypt(original)

    conn.execute(
        """
        INSERT INTO api_keys (provider, key_name, encrypted_key)
        VALUES ('OpenAI', 'db-round-trip', %s)
        """,
        (ciphertext,),
    )
    row = conn.execute(
        "SELECT encrypted_key FROM api_keys WHERE key_name = 'db-round-trip'"
    ).fetchone()

    assert row[0] == ciphertext
    assert encryption.decrypt(row[0]) == original
