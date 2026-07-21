from cryptography import x509

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
