"""Mandatory cross-cutting test: another tenant's object is always `404`.

Never `403` -- a `403` would confirm the object exists for someone else,
which is itself a leak (spec section 6). Parameterized across every
tenant-scoped GET endpoint this task implements.
"""

import pytest

from edutap.pass_builder.app import API_PREFIX
from edutap.pass_builder.models.enums import Scope

from .conftest import make_apple_bundle, seed_client


async def _owner_setup(client, session):
    """Seed tenant A's template/variant/version/credential. Returns their ids."""
    owner = await seed_client(session, [Scope.MANAGE, Scope.CREDENTIALS])

    template = (
        await client.post(
            f"{API_PREFIX}/templates",
            json={"key": "student-id", "name": "Student ID"},
            headers=owner.headers,
        )
    ).json()
    variant = (
        await client.post(
            f"{API_PREFIX}/templates/{template['id']}/variants",
            json={
                "key": "student",
                "name": "Student",
                "wallet_type": "apple",
                "is_default": True,
            },
            headers=owner.headers,
        )
    ).json()
    version = (
        await client.post(
            f"{API_PREFIX}/variants/{variant['id']}/versions",
            files={
                "file": (
                    "b.pkpasstemplate",
                    make_apple_bundle(),
                    "application/zip",
                )
            },
            headers=owner.headers,
        )
    ).json()
    credential = (
        await client.post(
            f"{API_PREFIX}/credentials",
            json={
                "provider": "apple",
                "label": "demo",
                "common_name": "Pass Type ID: pass.demo.lmu.de",
            },
            headers=owner.headers,
        )
    ).json()

    return {
        "template": template["id"],
        "variant": variant["id"],
        "version": version["id"],
        "credential": credential["id"],
    }


@pytest.mark.parametrize(
    "path_template",
    [
        f"{API_PREFIX}/templates/{{template}}",
        f"{API_PREFIX}/variants/{{variant}}",
        f"{API_PREFIX}/versions/{{version}}",
        f"{API_PREFIX}/versions/{{version}}/mappings",
        f"{API_PREFIX}/templates/{{template}}/variants",
        f"{API_PREFIX}/variants/{{variant}}/versions",
        f"{API_PREFIX}/versions/{{version}}/assets/icon.png",
    ],
)
async def test_other_tenant_gets_404_on_manage_endpoints(
    client, session, path_template
):
    ids = await _owner_setup(client, session)
    other = await seed_client(session, [Scope.MANAGE, Scope.CREDENTIALS])

    response = await client.get(path_template.format(**ids), headers=other.headers)
    assert response.status_code == 404
    assert response.status_code != 403


async def test_other_tenant_cannot_publish_foreign_version(client, session):
    ids = await _owner_setup(client, session)
    other = await seed_client(session, [Scope.MANAGE])

    response = await client.post(
        f"{API_PREFIX}/versions/{ids['version']}/publish", headers=other.headers
    )
    assert response.status_code == 404


async def test_other_tenant_cannot_modify_foreign_mappings(client, session):
    ids = await _owner_setup(client, session)
    other = await seed_client(session, [Scope.MANAGE])

    response = await client.put(
        f"{API_PREFIX}/versions/{ids['version']}/mappings",
        json={"rules": []},
        headers=other.headers,
    )
    assert response.status_code == 404


async def test_other_tenant_gets_404_on_credential_csr(client, session):
    ids = await _owner_setup(client, session)
    other = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.get(
        f"{API_PREFIX}/credentials/{ids['credential']}/csr", headers=other.headers
    )
    assert response.status_code == 404


async def test_other_tenant_gets_404_on_credential_revoke(client, session):
    ids = await _owner_setup(client, session)
    other = await seed_client(session, [Scope.CREDENTIALS])

    response = await client.delete(
        f"{API_PREFIX}/credentials/{ids['credential']}", headers=other.headers
    )
    assert response.status_code == 404


async def test_other_tenant_cannot_sync_foreign_variant(client, session):
    ids = await _owner_setup(client, session)
    other = await seed_client(session, [Scope.MANAGE])

    response = await client.post(
        f"{API_PREFIX}/variants/{ids['variant']}/sync", headers=other.headers
    )
    assert response.status_code == 404
    assert response.status_code != 403


async def test_other_tenant_cannot_render_foreign_template(client, session):
    await _owner_setup(client, session)
    other = await seed_client(session, [Scope.RENDER])

    response = await client.post(
        f"{API_PREFIX}/passes",
        json={
            "pass_id": "1",
            "template": "student-id",
            "wallet_type": "apple",
            "person_uid": "u1",
        },
        headers=other.headers,
    )
    assert response.status_code == 404
