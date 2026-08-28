"""Focused test: `Settings.wwdr_certificate_path` actually drives Apple signing.

`RenderService._apple_signer` used to sign via
`edutap.wallet_apple.api.sign_direct`, which ignores any WWDR certificate
handed to it and always loads its own from a *separate*
`edutap.wallet_apple.settings.Settings` (env prefix `EDUTAP_WALLET_APPLE_`) --
so this project's own `wwdr_certificate_path` setting had no effect on which
WWDR certificate production signing actually used. The fix makes
`RenderService` take `wwdr_certificate_path` via constructor injection (from
`Settings.wwdr_certificate_path`, wired in `dependencies.get_render_service`)
and sign through `PkPass.sign_direct(key, cert, wwdr)` directly, handing over
those exact bytes.

This test proves the wiring end to end, without needing Docker or a real
Apple credential: it signs the same pass with two *different* WWDR files and
checks the PKCS7 signature embeds whichever certificate
`wwdr_certificate_path` pointed at each time -- if the setting were still
dead, both signatures would embed the same (wrong, unrelated) certificate
regardless of what this test passes in.
"""

import base64
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs7
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.auth import AuthContext
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import (
    ApiClient,
    DataField,
    MappingRule,
    Template,
    TemplateVariant,
    TemplateVersion,
    Tenant,
)
from edutap.pass_builder.models.enums import (
    RuleOrigin,
    Scope,
    TargetKind,
    ValueType,
    VersionStatus,
    WalletType,
)
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.credentials import CredentialService
from edutap.pass_builder.services.render import RenderService
from edutap.pass_builder.services.templates import TemplateService

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_KEY_PEM = (_FIXTURES_DIR / "test_signing_key.pem").read_bytes()
_CERT_PEM = (_FIXTURES_DIR / "test_signing_cert.pem").read_bytes()
_WWDR_A = _FIXTURES_DIR / "wwdr-g4.pem"
# Any other PEM certificate does as a distinguishable "wrong" WWDR here --
# `crypto.create_keys` just parses whatever PEM it is given, with no chain
# validation, so `apple_cert.pem` (an unrelated leaf certificate already
# committed as a fixture) works fine as a second, differently-subjected cert.
_WWDR_B = _FIXTURES_DIR / "apple_cert.pem"


class FakeDataProvider:
    """Returns a fixed sample person for every lookup."""

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict:
        return {"person.name": "Ada Lovelace"}


class FakeObjectStore:
    """Unused by this test but required by `TemplateService`'s constructor."""

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise AssertionError("not expected to be called")

    async def get(self, key: str) -> bytes:
        raise AssertionError("not expected to be called")


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


@pytest.fixture
async def apple_env(session):
    """A tenant with a real, imported Apple credential and a published template."""
    backend = DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())

    tenant = Tenant(key="wwdr-test", name="WWDR Test Tenant")
    session.add(tenant)
    await session.flush()
    api_client = ApiClient(
        tenant_id=tenant.id,
        name="renderer",
        token_hash="unused",  # noqa: S106 - a test fixture value, not a secret
        scopes=[Scope.RENDER],
    )
    session.add(api_client)
    await session.flush()

    credentials = CredentialService(session, backend)
    credential_set = await credentials.import_apple(
        tenant.id, "test-apple", _KEY_PEM, _CERT_PEM
    )

    template = Template(tenant_id=tenant.id, key="student-id", name="Student ID")
    session.add(template)
    await session.flush()

    variant = TemplateVariant(
        template_id=template.id,
        wallet_type=WalletType.APPLE_VAS,
        key="student",
        name="Student",
        is_default=True,
        credential_set_id=credential_set.id,
    )
    session.add(variant)
    await session.flush()

    version = TemplateVersion(
        variant_id=variant.id,
        number=1,
        status=VersionStatus.PUBLISHED,
        pass_json={
            "formatVersion": 1,
            "description": "Student ID",
            "organizationName": "Test Org",
            "passTypeIdentifier": "pass.test.local",
            "teamIdentifier": "TEST123456",
            "generic": {
                "primaryFields": [{"key": "name", "label": "Name", "value": ""}]
            },
        },
    )
    session.add(version)
    await session.flush()

    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    session.add(
        MappingRule(
            version_id=version.id,
            origin=RuleOrigin.AUTHORED,
            target_kind=TargetKind.FIELD_VALUE,
            target="name",
            source_field="person.name",
            value_type=ValueType.TEXT,
            required=True,
            position=0,
        )
    )
    await session.flush()

    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )
    templates = TemplateService(session, FakeObjectStore())
    return auth, templates, credentials


def _embedded_certificate_subjects(pkpass_bytes: bytes) -> set[str]:
    """Return the RFC4514 subjects of every certificate embedded in the signature."""
    with zipfile.ZipFile(io.BytesIO(pkpass_bytes)) as archive:
        assert json.loads(archive.read("pass.json"))  # sanity: a real pass.json
        signature = archive.read("signature")
    embedded = pkcs7.load_der_pkcs7_certificates(signature)
    return {cert.subject.rfc4514_string() for cert in embedded}


def _make_service(
    session, templates, credentials, data_provider, *, wwdr_certificate_path
):
    """Build a `RenderService`, `data_provider` deliberately left untyped.

    Mirrors `tests/services/test_render.py::_make_service`: `DataProviderClient`
    is a concrete class rather than a protocol, so a real typechecker would
    (rightly) reject a `FakeDataProvider` passed straight to `RenderService`'s
    typed constructor. Routing it through this untyped parameter is this
    codebase's existing convention for that gap, not something new here.
    """
    return RenderService(
        session,
        templates,
        credentials,
        data_provider,
        wwdr_certificate_path=wwdr_certificate_path,
    )


async def _create_pass(session, apple_env, *, wwdr_certificate_path: Path):
    auth, templates, credentials = apple_env
    service = _make_service(
        session,
        templates,
        credentials,
        FakeDataProvider(),
        wwdr_certificate_path=wwdr_certificate_path,
    )
    return await service.create_pass(
        auth,
        pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
        template_key="student-id",
        wallet_type=WalletType.APPLE_VAS,
        variant_key=None,
        person_uid="u1",
        version_number=None,
    )


async def test_apple_signing_embeds_the_configured_wwdr_certificate(session, apple_env):
    """Signing with `wwdr_certificate_path=A` embeds A's certificate, not B's."""
    wwdr_a_cert = x509.load_pem_x509_certificate(_WWDR_A.read_bytes())
    wwdr_b_cert = x509.load_pem_x509_certificate(_WWDR_B.read_bytes())
    assert wwdr_a_cert.subject != wwdr_b_cert.subject  # sanity: genuinely different

    result = await _create_pass(session, apple_env, wwdr_certificate_path=_WWDR_A)

    subjects = _embedded_certificate_subjects(result.pkpass)
    assert wwdr_a_cert.subject.rfc4514_string() in subjects
    assert wwdr_b_cert.subject.rfc4514_string() not in subjects


async def test_apple_signing_switches_wwdr_when_the_setting_changes(session, apple_env):
    """Pointing the *same* service config at a different WWDR changes the embedded cert.

    This is the crux of the wiring proof: if `wwdr_certificate_path` were
    still dead (as it was before the fix -- see `RenderService._apple_signer`
    docstring), this render would embed whatever WWDR the *other*, unrelated
    `edutap.wallet_apple` settings happened to resolve, regardless of what
    this test passes in -- not `_WWDR_B`.
    """
    wwdr_b_cert = x509.load_pem_x509_certificate(_WWDR_B.read_bytes())

    result = await _create_pass(session, apple_env, wwdr_certificate_path=_WWDR_B)

    subjects = _embedded_certificate_subjects(result.pkpass)
    assert wwdr_b_cert.subject.rfc4514_string() in subjects


async def test_apple_signing_fails_closed_when_configured_wwdr_path_is_missing(
    session, apple_env, tmp_path
):
    """A misconfigured `wwdr_certificate_path` fails the render, not a silent fallback.

    Confirms there is no hidden fallback to some other (e.g.
    `edutap.wallet_apple`-owned) WWDR file when ours does not resolve -- the
    setting is the *only* source now.
    """
    missing_path = tmp_path / "does-not-exist.pem"

    with pytest.raises(ProblemError) as excinfo:
        await _create_pass(session, apple_env, wwdr_certificate_path=missing_path)

    assert excinfo.value.status == 500
    assert excinfo.value.slug == "internal_error"
