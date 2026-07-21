"""Tests for the fields, audit, health and readiness endpoints."""

from edutap.pass_builder.clients.data_provider import CatalogueField
from edutap.pass_builder.models.db import AuditLog
from edutap.pass_builder.models.enums import Scope

from .conftest import seed_client


async def test_healthz_is_open(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_ready_when_all_checks_pass(client):
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_readyz_is_503_when_data_provider_unreachable(client, data_provider):
    data_provider.fail = True

    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["type"].endswith("not_ready")
    assert body["checks"]["data_provider"] is False
    assert body["checks"]["database"] is True


async def test_refresh_fields_replaces_catalogue(client, session, data_provider):
    manager = await seed_client(session, [Scope.MANAGE])
    data_provider.catalogue = [
        CatalogueField(key="person.name", value_type="text", label="Name")
    ]

    response = await client.post("/api/v1/fields/refresh", headers=manager.headers)
    assert response.status_code == 200
    assert response.json() == [
        {
            "key": "person.name",
            "value_type": "text",
            "label": "Name",
            "required": False,
            "description": None,
        }
    ]

    listed = await client.get("/api/v1/fields", headers=manager.headers)
    assert listed.status_code == 200
    assert listed.json()[0]["key"] == "person.name"


async def test_list_fields_requires_manage_scope(client, session):
    renderer = await seed_client(session, [Scope.RENDER])

    response = await client.get("/api/v1/fields", headers=renderer.headers)
    assert response.status_code == 403


async def test_audit_lists_only_own_tenant(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    other = await seed_client(session, [Scope.MANAGE])
    session.add(
        AuditLog(
            tenant_id=manager.tenant_id,
            request_id="req-1",
            actor_client_id=None,
            action="pass.create",
            outcome="success",
            duration_ms=1,
            requested_fields=["person.name"],
        )
    )
    session.add(
        AuditLog(
            tenant_id=other.tenant_id,
            request_id="req-2",
            actor_client_id=None,
            action="pass.create",
            outcome="success",
            duration_ms=1,
            requested_fields=[],
        )
    )
    await session.flush()

    response = await client.get("/api/v1/audit", headers=manager.headers)
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["request_id"] == "req-1"


async def test_audit_filters_by_outcome(client, session):
    manager = await seed_client(session, [Scope.MANAGE])
    session.add(
        AuditLog(
            tenant_id=manager.tenant_id,
            request_id="req-ok",
            actor_client_id=None,
            action="pass.create",
            outcome="success",
            duration_ms=1,
            requested_fields=[],
        )
    )
    session.add(
        AuditLog(
            tenant_id=manager.tenant_id,
            request_id="req-fail",
            actor_client_id=None,
            action="pass.create",
            outcome="error",
            error_code="missing_field",
            duration_ms=1,
            requested_fields=[],
        )
    )
    await session.flush()

    response = await client.get("/api/v1/audit?outcome=error", headers=manager.headers)
    assert response.status_code == 200
    [entry] = response.json()
    assert entry["request_id"] == "req-fail"
