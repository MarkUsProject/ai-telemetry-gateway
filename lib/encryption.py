"""Encryption for upstream provider keys stored in the api_keys table.

API keys must be encrypted at rest so a database dump exposes no usable
credentials. We use Fernet (AES-128-CBC with HMAC-SHA-256
authentication) from the `cryptography` library — the standard ergonomic
choice for symmetric encryption in Python.

The key is sourced from the AITG_ENCRYPTION_KEY environment variable. This
is a separate secret from LITELLM_SALT_KEY so a rotation of LiteLLM's
internal salt does not invalidate our column ciphertexts.

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

PROD: AITG_ENCRYPTION_KEY is sourced from sysadmin's secret store, same
mechanism as the other secrets in .env.example.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "AITG_ENCRYPTION_KEY"


def _load_cipher() -> Fernet:
    key = os.environ.get(_ENV_VAR)
    if not key:
        raise RuntimeError(f"{_ENV_VAR} is not set. See module docstring for how to generate one.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"{_ENV_VAR} is not a valid Fernet key.") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext API key. Returns a string suitable for TEXT storage."""
    if not plaintext:
        raise ValueError("Cannot encrypt empty plaintext.")
    cipher = _load_cipher()
    return cipher.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a previously encrypted API key. Raises on tamper or wrong key."""
    if not ciphertext:
        raise ValueError("Cannot decrypt empty ciphertext.")
    cipher = _load_cipher()
    try:
        return cipher.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Decryption failed. Either the ciphertext was tampered with or "
            f"{_ENV_VAR} differs from the one used at encryption time."
        ) from exc
