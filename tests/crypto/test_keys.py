from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509.oid import NameOID

from edutap.pass_builder.crypto.certificates import certificate_matches_key
from edutap.pass_builder.crypto.keys import build_csr, generate_private_key


def test_generated_key_is_rsa_2048_pem():
    key_pem = generate_private_key()
    assert b"BEGIN PRIVATE KEY" in key_pem


def test_csr_carries_the_common_name_and_matches_the_key():
    key_pem = generate_private_key()
    csr_pem = build_csr(key_pem, "Pass Type ID: pass.demo.lmu.de")
    csr = x509.load_pem_x509_csr(csr_pem)
    assert csr.is_signature_valid
    assert "pass.demo.lmu.de" in csr.subject.rfc4514_string()


def test_certificate_matches_its_own_key():
    key_pem = generate_private_key()
    key_obj = load_pem_private_key(key_pem, password=None)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Test Certificate")]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key_obj.public_key())  # ty: ignore[invalid-argument-type]
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key_obj, hashes.SHA256())  # ty: ignore[invalid-argument-type]
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    assert certificate_matches_key(cert_pem, key_pem) is True


def test_certificate_does_not_match_a_different_key():
    key_a_pem = generate_private_key()
    key_b_pem = generate_private_key()
    key_a_obj = load_pem_private_key(key_a_pem, password=None)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Test Certificate")]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key_a_obj.public_key())  # ty: ignore[invalid-argument-type]
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key_a_obj, hashes.SHA256())  # ty: ignore[invalid-argument-type]
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    assert certificate_matches_key(cert_pem, key_b_pem) is False
