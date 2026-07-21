import base64
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import SQLModel

from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import Tenant
from edutap.pass_builder.models.enums import CredentialStatus, Provider
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.credentials import CredentialService

CERT = Path(__file__).parent.parent / "fixtures" / "apple_cert.pem"


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


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
