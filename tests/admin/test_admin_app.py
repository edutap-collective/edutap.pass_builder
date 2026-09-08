"""The admin application: the management routers, a third time, behind a person."""

from edutap.pass_builder.admin.app import ADMIN_PREFIX

from .conftest import UB_KEY, as_person


async def test_without_an_asserted_person_everything_is_401(client, tenants):
    response = await client.get(f"{ADMIN_PREFIX}/tenants")
    assert response.status_code == 401


async def test_an_authenticated_person_sees_the_tenant_list(client, tenants):
    response = await client.get(f"{ADMIN_PREFIX}/tenants", headers=as_person("nobody"))
    assert response.status_code == 200
    assert {t["key"] for t in response.json()} == {"lmu-ub", "stwm"}


async def test_reading_templates_needs_the_read_permission_in_that_tenant(
    client, tenants
):
    ub = tenants[UB_KEY]
    url = f"{ADMIN_PREFIX}/tenants/{ub.id}/templates"
    assert (await client.get(url, headers=as_person("ub-readers"))).status_code == 200
    assert (await client.get(url, headers=as_person("auditors"))).status_code == 403


async def test_the_refusal_names_the_permission_with_the_tenant_key(client, tenants):
    ub = tenants[UB_KEY]
    response = await client.get(
        f"{ADMIN_PREFIX}/tenants/{ub.id}/templates", headers=as_person("auditors")
    )
    assert "templates:read@lmu-ub" in response.text


async def test_read_does_not_grant_write(client, tenants):
    ub = tenants[UB_KEY]
    response = await client.post(
        f"{ADMIN_PREFIX}/tenants/{ub.id}/templates",
        headers=as_person("ub-readers"),
        json={"key": "t1", "name": "T1", "pass_kind": "generic"},
    )
    assert response.status_code == 403


async def test_an_unknown_tenant_id_is_a_404(client, tenants):
    response = await client.get(
        f"{ADMIN_PREFIX}/tenants/00000000-0000-0000-0000-000000000000/templates",
        headers=as_person("ub-admins"),
    )
    assert response.status_code == 404


async def test_rendering_passes_is_not_mounted(client, tenants):
    response = await client.post(
        f"{ADMIN_PREFIX}/passes", headers=as_person("ub-admins"), json={}
    )
    assert response.status_code in (404, 405)
