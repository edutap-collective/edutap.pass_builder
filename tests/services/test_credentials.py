import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import Tenant
from edutap.pass_builder.models.enums import CredentialStatus, Provider
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.credentials import CredentialService

CERT = Path(__file__).parent.parent / "fixtures" / "apple_cert.pem"


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


def service(session) -> CredentialService:
    backend = DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())
    return CredentialService(session, backend)


async def a_tenant(session) -> Tenant:
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    return tenant


async def test_create_apple_yields_key_pending_with_csr(session):
    tenant = await a_tenant(session)
    svc = service(session)
    cred = await svc.create_apple(tenant.id, "demo", "Pass Type ID: pass.demo.lmu.de")
    assert cred.status == CredentialStatus.KEY_PENDING
    assert "BEGIN CERTIFICATE REQUEST" in cred.csr_pem  # ty: ignore[unsupported-operator]


async def test_install_mismatched_certificate_is_rejected(session):
    tenant = await a_tenant(session)
    svc = service(session)
    cred = await svc.create_apple(tenant.id, "demo", "Pass Type ID: pass.demo.lmu.de")
    with pytest.raises(ProblemError) as excinfo:
        await svc.install_certificate(tenant.id, cred.id, CERT.read_bytes())
    assert excinfo.value.slug == "certificate_key_mismatch"


async def test_open_material_round_trips(session):
    """The plaintext key returned by open_material matches what was sealed."""
    tenant = await a_tenant(session)
    svc = service(session)
    cred = await svc.create_apple(tenant.id, "demo", "Pass Type ID: pass.demo.lmu.de")

    plaintext = await svc.open_material(cred)

    assert b"BEGIN PRIVATE KEY" in plaintext


async def test_list_sets_filters_by_expiry(session):
    """Only credential sets expiring within the window are returned."""
    tenant = await a_tenant(session)
    svc = service(session)
    now = datetime.now(UTC)

    soon = await svc.create_apple(tenant.id, "soon", "Pass Type ID: pass.soon.lmu.de")
    soon.not_after = now + timedelta(days=5)
    session.add(soon)

    later = await svc.create_apple(
        tenant.id, "later", "Pass Type ID: pass.later.lmu.de"
    )
    later.not_after = now + timedelta(days=90)
    session.add(later)
    await session.flush()

    results = await svc.list_sets(tenant.id, expiring_within_days=30)

    labels = {c.label for c in results}
    assert labels == {"soon"}


async def test_list_sets_filters_by_provider(session):
    """Only credential sets of the requested provider are returned."""
    tenant = await a_tenant(session)
    svc = service(session)
    await svc.create_apple(tenant.id, "demo", "Pass Type ID: pass.demo.lmu.de")

    results = await svc.list_sets(tenant.id, provider=Provider.GOOGLE)

    assert results == []


async def test_get_csr_missing_credential_raises_404(session):
    tenant = await a_tenant(session)
    svc = service(session)

    with pytest.raises(ProblemError) as excinfo:
        await svc.get_csr(tenant.id, uuid4())
    assert excinfo.value.status == 404
    assert excinfo.value.slug == "credential_not_found"


async def test_import_google_stores_metadata_and_material(session):
    tenant = await a_tenant(session)
    svc = service(session)
    service_account = {
        "client_email": "svc@proj.iam.gserviceaccount.com",
        "private_key_id": "kid-1",
        "project_id": "proj",
        "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
    }
    raw = json.dumps(service_account).encode()

    cred = await svc.import_google(tenant.id, "google-demo", raw, issuer_id="3388")

    assert cred.provider == Provider.GOOGLE
    assert cred.status == CredentialStatus.ACTIVE
    assert cred.service_account_email == "svc@proj.iam.gserviceaccount.com"
    assert cred.issuer_id == "3388"

    material = await svc.open_material(cred)
    assert material == raw


async def test_import_google_rejects_malformed_json(session):
    tenant = await a_tenant(session)
    svc = service(session)

    with pytest.raises(ProblemError) as excinfo:
        await svc.import_google(tenant.id, "bad", b"not json at all", issuer_id="3388")

    assert excinfo.value.slug == "invalid_service_account"
    assert excinfo.value.status == 422


async def test_credentials_are_tenant_isolated(session):
    tenant_a = await a_tenant(session)
    tenant_b = Tenant(key="other", name="Other Tenant")
    session.add(tenant_b)
    await session.flush()
    svc = service(session)

    cred = await svc.create_apple(tenant_a.id, "demo", "Pass Type ID: pass.demo.lmu.de")

    with pytest.raises(ProblemError) as excinfo:
        await svc.get_csr(tenant_b.id, cred.id)
    assert excinfo.value.status == 404

    with pytest.raises(ProblemError) as excinfo:
        await svc.install_certificate(tenant_b.id, cred.id, CERT.read_bytes())
    assert excinfo.value.status == 404

    assert await svc.list_sets(tenant_b.id) == []
    results = await svc.list_sets(tenant_a.id)
    assert [c.id for c in results] == [cred.id]
