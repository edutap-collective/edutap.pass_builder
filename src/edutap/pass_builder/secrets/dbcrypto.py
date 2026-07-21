"""Envelope encryption of secrets for storage in the database."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .backend import SealedSecret


class DatabaseSecretBackend:
    """Encrypt secrets with a per-secret data key wrapped by a master key."""

    def __init__(self, master_key_b64: str) -> None:
        """Initialize the backend with a base64-encoded 32-byte master key."""
        master_key = base64.b64decode(master_key_b64)
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes (base64 encoded)")
        self._master = AESGCM(master_key)

    def seal(self, plaintext: bytes) -> SealedSecret:
        """Encrypt the plaintext under a fresh data key."""
        dek = os.urandom(32)
        nonce = os.urandom(12)
        wrap_nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, None)
        wrapped = wrap_nonce + self._master.encrypt(wrap_nonce, dek, None)
        return SealedSecret(ciphertext=ciphertext, nonce=nonce, wrapped_dek=wrapped)

    def open(self, sealed: SealedSecret) -> bytes:
        """Decrypt a sealed secret."""
        wrap_nonce, wrapped = sealed.wrapped_dek[:12], sealed.wrapped_dek[12:]
        dek = self._master.decrypt(wrap_nonce, wrapped, None)
        return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, None)
