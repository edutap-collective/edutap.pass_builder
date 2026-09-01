import pytest

from edutap.pass_builder.settings import Settings, get_settings
from edutap.pass_builder.ui.auth import Principal, is_authorised

from .conftest import AUTHORISED, AUTHORISED_GROUP


def settings_with(monkeypatch, *, users: str = "", groups: str = "") -> Settings:
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_UI_AUTHORISED_USERS", users)
    monkeypatch.setenv("EDUTAP_PASS_BUILDER_UI_AUTHORISED_GROUPS", groups)
    get_settings.cache_clear()
    return Settings()


def test_an_empty_allow_list_denies_everyone(monkeypatch):
    """An installation nobody configured must end up unreachable.

    The opposite default is an administration interface for signing
    credentials standing open, with nothing about the deployment looking
    wrong.
    """
    settings = settings_with(monkeypatch)
    assert not is_authorised(Principal(name="anyone", groups=frozenset()), settings)


def test_a_named_principal_is_allowed(monkeypatch):
    settings = settings_with(monkeypatch, users="ada@example.org, grace@example.org")
    assert is_authorised(
        Principal(name="grace@example.org", groups=frozenset()), settings
    )


def test_group_membership_is_enough_without_being_named(monkeypatch):
    """The path from one named person to a group, with no code change."""
    settings = settings_with(monkeypatch, groups="wallet-admins")
    assert is_authorised(
        Principal(name="nobody@example.org", groups=frozenset({"wallet-admins"})),
        settings,
    )


def test_a_different_group_is_not_enough(monkeypatch):
    settings = settings_with(monkeypatch, groups="wallet-admins")
    assert not is_authorised(
        Principal(name="nobody@example.org", groups=frozenset({"students"})), settings
    )


@pytest.mark.parametrize("route", ["/tenants"])
async def test_no_principal_is_401(anonymous_ui, route):
    """Reaching this means the request did not pass the web frontend."""
    response = await anonymous_ui.get(route)
    assert response.status_code == 401
    assert response.json()["type"].endswith("unauthenticated")


async def test_an_unlisted_principal_is_403(ui_app):
    from httpx import ASGITransport, AsyncClient

    from edutap.pass_builder.ui.app import UI_PREFIX

    transport = ASGITransport(app=ui_app)
    async with AsyncClient(
        transport=transport,
        base_url=f"http://ui{UI_PREFIX}",
        headers={"REMOTE_USER": "stranger@example.org"},
    ) as client:
        response = await client.get("/tenants")
    assert response.status_code == 403
    assert response.json()["type"].endswith("not_authorised")


async def test_the_groups_header_is_semicolon_separated(ui_app):
    """Shibboleth's default join for a multi-valued attribute."""
    from httpx import ASGITransport, AsyncClient

    from edutap.pass_builder.ui.app import UI_PREFIX

    transport = ASGITransport(app=ui_app)
    async with AsyncClient(
        transport=transport,
        base_url=f"http://ui{UI_PREFIX}",
        headers={
            "REMOTE_USER": "member@example.org",
            "isMemberOf": f"students;{AUTHORISED_GROUP};staff",
        },
    ) as client:
        response = await client.get("/tenants")
    assert response.status_code == 200


async def test_the_allow_listed_principal_gets_through(ui):
    response = await ui.get("/tenants")
    assert response.status_code == 200
    assert response.json() == []
    assert AUTHORISED  # the fixture's principal, asserted on every request
