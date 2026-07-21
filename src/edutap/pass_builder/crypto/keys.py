"""Generate private keys and certificate signing requests."""

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509 import CertificateSigningRequestBuilder, Name, NameAttribute
from cryptography.x509.oid import NameOID


def generate_private_key() -> bytes:
    """Return a fresh unencrypted RSA-2048 private key in PKCS#8 PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def build_csr(key_pem: bytes, common_name: str) -> bytes:
    """Return a CSR in PEM for the given key and common name."""
    key = load_pem_private_key(key_pem, password=None)
    subject = Name([NameAttribute(NameOID.COMMON_NAME, common_name)])
    # We generate the key ourselves, so it is always an RSA key here, even
    # though load_pem_private_key()'s return type also covers DH keys,
    # which CertificateSigningRequestBuilder.sign() does not accept.
    csr = (
        CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(key, hashes.SHA256())  # ty: ignore[invalid-argument-type]
    )
    return csr.public_bytes(serialization.Encoding.PEM)
