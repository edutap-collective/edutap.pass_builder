"""Generate a self-signed Apple-style test certificate chain.

Produces three PEM files used only by `tests/integration/test_end_to_end.py`:

- `test_signing_key.pem` -- the leaf (pass-type) private key, unencrypted
  PKCS#8.
- `test_signing_cert.pem` -- the leaf certificate, signed by the local test
  CA below. Its subject mirrors a real Apple pass-type certificate closely
  enough for `crypto.certificates.parse_apple_certificate` to extract the
  same fields (`UID` -> pass type identifier, `OU` -> team identifier,
  `O` -> organization name), and it carries Apple's NFC extension OID so
  `nfc_capable` comes back `True`.
- `wwdr-g4.pem` -- the self-signed CA certificate, standing in for Apple's
  real WWDR intermediate.

None of this is real Apple credential material -- it is entirely
self-signed, generated locally, and safe to commit. Run directly to
(re)write the fixtures:

    uv run python tests/fixtures/make_test_certs.py

A wide, fixed validity window (2024-01-01 to 2034-01-01) avoids the
generated certificates ever expiring during CI runs, without needing
`datetime.now()` at generation or verification time.
"""

from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import NameOID, ObjectIdentifier

_FIXTURES_DIR = Path(__file__).parent

_NFC_EXTENSION_OID = ObjectIdentifier("1.2.840.113635.100.6.1.26")

_NOT_BEFORE = datetime(2024, 1, 1, tzinfo=UTC)
_NOT_AFTER = datetime(2034, 1, 1, tzinfo=UTC)


def _generate_key() -> RSAPrivateKey:
    """Return a fresh RSA-2048 private key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_to_pem(key: RSAPrivateKey) -> bytes:
    """Return `key` as unencrypted PKCS#8 PEM."""
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _build_ca_certificate(ca_key: RSAPrivateKey) -> x509.Certificate:
    """Return a self-signed CA certificate standing in for Apple's WWDR."""
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Test WWDR CA"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "G4"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "eduTAP Test"),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        ]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_NOT_BEFORE)
        .not_valid_after(_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    return builder.sign(ca_key, hashes.SHA256())


def _build_leaf_certificate(
    leaf_key: RSAPrivateKey,
    ca_key: RSAPrivateKey,
    ca_certificate: x509.Certificate,
) -> x509.Certificate:
    """Return a leaf pass-type certificate signed by the test CA.

    The subject mirrors a real Apple pass-type certificate: `UID` carries
    the pass type identifier, `OU` the team identifier, `O` the
    organization name -- exactly what
    `crypto.certificates.parse_apple_certificate` reads back out.
    """
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.USER_ID, "pass.test.local"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Pass Type ID: pass.test.local"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "TEST123456"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "eduTAP Test"),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        ]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_certificate.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_NOT_BEFORE)
        .not_valid_after(_NOT_AFTER)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            # Apple's marker for NFC-capable pass-type certificates. Only
            # presence matters to `parse_apple_certificate` -- the payload
            # is an arbitrary DER NULL.
            x509.UnrecognizedExtension(_NFC_EXTENSION_OID, b"\x05\x00"),
            critical=False,
        )
    )
    return builder.sign(ca_key, hashes.SHA256())


def _certificate_to_pem(certificate: x509.Certificate) -> bytes:
    """Return `certificate` as PEM bytes."""
    return certificate.public_bytes(serialization.Encoding.PEM)


def generate(out_dir: Path = _FIXTURES_DIR) -> None:
    """Generate and write the three test PEM fixtures into `out_dir`."""
    ca_key = _generate_key()
    ca_certificate = _build_ca_certificate(ca_key)

    leaf_key = _generate_key()
    leaf_certificate = _build_leaf_certificate(leaf_key, ca_key, ca_certificate)

    (out_dir / "test_signing_key.pem").write_bytes(_key_to_pem(leaf_key))
    (out_dir / "test_signing_cert.pem").write_bytes(
        _certificate_to_pem(leaf_certificate)
    )
    (out_dir / "wwdr-g4.pem").write_bytes(_certificate_to_pem(ca_certificate))


if __name__ == "__main__":
    generate()
    print(f"Wrote test certificate fixtures to {_FIXTURES_DIR}")
