"""Round-trip tests for the AES-GCM envelope secret backend."""

import base64
import os

import pytest

from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend


def make_backend() -> DatabaseSecretBackend:
    """Build a backend with a fresh random master key."""
    return DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())


def test_seal_then_open_returns_the_plaintext():
    """Sealing then opening returns the original plaintext."""
    backend = make_backend()
    sealed = backend.seal(b"super-secret-key")
    assert backend.open(sealed) == b"super-secret-key"


def test_each_seal_uses_a_fresh_nonce_and_dek():
    """Sealing the same plaintext twice uses a fresh nonce and DEK each time."""
    backend = make_backend()
    a = backend.seal(b"same")
    b = backend.seal(b"same")
    assert a.ciphertext != b.ciphertext
    assert a.nonce != b.nonce
    assert a.wrapped_dek != b.wrapped_dek


def test_tampered_ciphertext_is_rejected():
    """A tampered ciphertext byte causes open() to raise on GCM auth failure."""
    backend = make_backend()
    sealed = backend.seal(b"x")
    sealed.ciphertext = sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1])
    with pytest.raises(Exception):  # noqa: B017 -- brief pins this exact assertion
        backend.open(sealed)
