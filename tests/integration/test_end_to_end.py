"""End-to-end integration test: real credential, real template, real signing.

Exercises the full stack -- import an Apple credential, publish a template
with a mapping rule, render a pass through `RenderService` against a fake
data provider -- and checks the resulting `.pkpass` was genuinely signed
with the imported credential.

Real chain verification via `edutap.wallet_apple.api.verify` needs
`cryptography`'s PKCS7 "NoVerify" support (`PKCS7Options.NoVerify`, added by
an as-yet-unmerged upstream PR --
https://github.com/pyca/cryptography/pull/12116). This repo's pinned
`cryptography` build does not have it yet
(`edutap.wallet_apple.crypto.supports_verification()` returns `False`), so
`api.verify` currently raises `AttributeError` for *any* pass, self-signed
or Apple-issued -- it is not specific to the test WWDR substitute used here.
This test prefers the real check the moment that support lands
(`supports_verification()` is checked at run time, not hardcoded), and
falls back to asserting everything that *can* be verified without it: the
`.pkpass` is a well-formed ZIP whose `manifest.json` hashes match its files
exactly, and whose `signature` is a genuine PKCS7 structure embedding our
test leaf and CA certificates -- i.e. the pass was correctly assembled and
signed with the imported credential, even though full chain verification is
unavailable in this environment.
"""

import io
import json
import zipfile
from hashlib import sha1
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs7
from edutap.wallet_apple import api
from edutap.wallet_apple.crypto import supports_verification

from edutap.pass_builder.models.enums import WalletType

pytestmark = pytest.mark.integration

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _assert_manifest_hashes_match(archive: zipfile.ZipFile) -> None:
    """Recompute every manifest-listed file's sha1 and compare to the manifest."""
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest, "manifest.json must list at least one file"
    for filename, expected_sha1 in manifest.items():
        actual_sha1 = sha1(archive.read(filename)).hexdigest()  # noqa: S324
        assert actual_sha1 == expected_sha1, f"hash mismatch for {filename}"


def _assert_signature_embeds_test_identity(signature: bytes) -> None:
    """Check the PKCS7 signature carries our test leaf and CA certificates.

    This does not verify the signature cryptographically (that needs the
    unavailable `NoVerify` support -- see the module docstring); it proves
    the blob is a genuine PKCS7 structure produced with our specific test
    credential, rather than merely non-empty bytes.
    """
    embedded = pkcs7.load_der_pkcs7_certificates(signature)
    subjects = {cert.subject.rfc4514_string() for cert in embedded}
    leaf_cert = x509.load_pem_x509_certificate(
        (_FIXTURES_DIR / "test_signing_cert.pem").read_bytes()
    )
    ca_cert = x509.load_pem_x509_certificate(
        (_FIXTURES_DIR / "wwdr-g4.pem").read_bytes()
    )
    assert leaf_cert.subject.rfc4514_string() in subjects
    assert ca_cert.subject.rfc4514_string() in subjects


def _assert_pkpass_is_validly_signed(pkpass_bytes: bytes) -> None:
    """Fallback proof of a real signature -- see the module docstring for why."""
    with zipfile.ZipFile(io.BytesIO(pkpass_bytes)) as archive:
        names = set(archive.namelist())
        assert {"pass.json", "manifest.json", "signature"} <= names
        _assert_manifest_hashes_match(archive)
        _assert_signature_embeds_test_identity(archive.read("signature"))


async def test_apple_pass_is_built_signed_and_verifies(e2e_env):
    """Import credential + publish template + render => a validly signed pass."""
    result = await e2e_env.create_apple_pass(person_uid="u1")

    assert result.wallet_type == WalletType.APPLE
    assert result.pkpass is not None
    assert result.credential_set == "e2e-apple"

    if supports_verification():
        pkpass = api.new(file=io.BytesIO(result.pkpass))
        api.verify(pkpass, settings=e2e_env.settings)  # raises if broken
    else:
        _assert_pkpass_is_validly_signed(result.pkpass)

    with zipfile.ZipFile(io.BytesIO(result.pkpass)) as archive:
        pass_json = json.loads(archive.read("pass.json"))
    [name_field] = [
        field
        for field in pass_json["generic"]["primaryFields"]
        if field["key"] == "name"
    ]
    assert name_field["value"] == "Ada Lovelace"
