"""The secret backend protocol."""

from typing import Protocol

from pydantic import BaseModel


class SealedSecret(BaseModel):
    """An encrypted secret ready to be stored in the database."""

    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    algo: str = "AES-256-GCM"


class SecretBackend(Protocol):
    """Seals and opens secret material."""

    def seal(self, plaintext: bytes) -> SealedSecret:
        """Encrypt the plaintext."""
        ...

    def open(self, sealed: SealedSecret) -> bytes:
        """Decrypt a sealed secret."""
        ...
