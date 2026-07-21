"""Tests for the audit log endpoint: tenant scoping and pagination.

The audit table grows with every render, so `GET /api/v1/audit` must never
return an unbounded result set -- see `routers/audit.py::list_audit`'s
`limit`/`offset` query params.
"""

from datetime import UTC, datetime, timedelta

from edutap.pass_builder.models.db import AuditLog
from edutap.pass_builder.models.enums import Scope

from .conftest import seed_client


async def _add_audit_rows(session, tenant_id, count: int) -> None:
    """Add `count` audit rows for a tenant, each with a distinct, increasing `ts`.

    Explicit, strictly increasing timestamps (rather than relying on
    `AuditLog.ts`'s `default_factory`) make `ORDER BY ts DESC` -- and so
    which rows land on which `limit`/`offset` page -- deterministic, since
    rows created in the same flush could otherwise tie at whatever
    resolution the wall clock happens to offer.
    """
    base = datetime.now(UTC)
    for i in range(count):
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                ts=base + timedelta(seconds=i),
                request_id=f"req-{i}",
                actor_client_id=None,
                action="pass.create",
                outcome="success",
                error_code=None,
                duration_ms=1,
                template_id=None,
                variant_id=None,
                version_id=None,
                wallet_type=None,
                subject_ref=None,
                requested_fields=[],
            )
        )
    await session.flush()


async def test_list_audit_returns_every_row_under_the_default_limit(client, session):
    """Fewer entries than the default `limit` are all returned."""
    manager = await seed_client(session, [Scope.MANAGE])
    await _add_audit_rows(session, manager.tenant_id, 5)

    response = await client.get("/api/v1/audit", headers=manager.headers)

    assert response.status_code == 200
    assert len(response.json()) == 5


async def test_list_audit_limit_caps_the_result_set(client, session):
    """`limit` bounds the number of rows returned, even when more exist."""
    manager = await seed_client(session, [Scope.MANAGE])
    await _add_audit_rows(session, manager.tenant_id, 10)

    response = await client.get(
        "/api/v1/audit", params={"limit": 3}, headers=manager.headers
    )

    assert response.status_code == 200
    assert len(response.json()) == 3


async def test_list_audit_orders_newest_first_under_a_limit(client, session):
    """`limit` still returns the newest entries, not an arbitrary subset."""
    manager = await seed_client(session, [Scope.MANAGE])
    await _add_audit_rows(session, manager.tenant_id, 5)

    response = await client.get(
        "/api/v1/audit", params={"limit": 2}, headers=manager.headers
    )

    request_ids = [entry["request_id"] for entry in response.json()]
    assert request_ids == ["req-4", "req-3"]


async def test_list_audit_offset_pages_past_the_first_results(client, session):
    """`offset` moves the window forward without repeating or dropping rows."""
    manager = await seed_client(session, [Scope.MANAGE])
    await _add_audit_rows(session, manager.tenant_id, 5)

    first_page = (
        await client.get(
            "/api/v1/audit",
            params={"limit": 2, "offset": 0},
            headers=manager.headers,
        )
    ).json()
    second_page = (
        await client.get(
            "/api/v1/audit",
            params={"limit": 2, "offset": 2},
            headers=manager.headers,
        )
    ).json()

    assert [e["request_id"] for e in first_page] == ["req-4", "req-3"]
    assert [e["request_id"] for e in second_page] == ["req-2", "req-1"]


async def test_list_audit_rejects_a_limit_over_the_maximum(client, session):
    """A `limit` over the hard cap of 1000 is a validation error, not clamped."""
    manager = await seed_client(session, [Scope.MANAGE])

    response = await client.get(
        "/api/v1/audit", params={"limit": 1001}, headers=manager.headers
    )

    assert response.status_code == 422


async def test_list_audit_only_shows_own_tenant(client, session):
    """Pagination never leaks another tenant's audit rows into the count."""
    manager = await seed_client(session, [Scope.MANAGE])
    other = await seed_client(session, [Scope.MANAGE])
    await _add_audit_rows(session, other.tenant_id, 5)

    response = await client.get("/api/v1/audit", headers=manager.headers)

    assert response.status_code == 200
    assert response.json() == []
