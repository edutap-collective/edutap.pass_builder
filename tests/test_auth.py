import pytest
from starlette.requests import Request
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.auth import (
    AuthContext,
    current_auth,
    hash_token,
    require,
    resolve_token,
)
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import ApiClient, Tenant
from edutap.pass_builder.models.enums import Scope


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


async def seed(session) -> str:
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    session.add(
        ApiClient(
            tenant_id=tenant.id,
            name="mgr",
            token_hash=hash_token("secret-token"),
            scopes=[Scope.MANAGE],
            active=True,
        )
    )
    await session.flush()
    return "secret-token"


async def test_valid_token_resolves_to_context(session):
    token = await seed(session)
    context = await resolve_token(session, token)
    assert isinstance(context, AuthContext)
    assert Scope.MANAGE in context.scopes


async def test_unknown_token_is_unauthenticated(session):
    with pytest.raises(ProblemError) as excinfo:
        await resolve_token(session, "nope")
    assert excinfo.value.status == 401


async def test_missing_bearer_header_is_unauthenticated(session):
    """Request with missing or malformed Authorization header returns 401."""
    await seed(session)

    # Test with missing Authorization header
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [],
    }
    request = Request(scope)
    with pytest.raises(ProblemError) as excinfo:
        await current_auth(request, session)
    assert excinfo.value.status == 401
    assert excinfo.value.slug == "unauthenticated"

    # Test with Authorization header that doesn't start with "Bearer "
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", b"Basic xyz")],
    }
    request = Request(scope)
    with pytest.raises(ProblemError) as excinfo:
        await current_auth(request, session)
    assert excinfo.value.status == 401
    assert excinfo.value.slug == "unauthenticated"


async def test_insufficient_scope_is_forbidden(session):
    """A valid token with insufficient scope returns 403."""
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    session.add(
        ApiClient(
            tenant_id=tenant.id,
            name="renderer",
            token_hash=hash_token("render-token"),
            scopes=[Scope.RENDER],
            active=True,
        )
    )
    await session.flush()

    dependency = require(Scope.MANAGE)

    # Token with RENDER scope calling endpoint requiring MANAGE
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", b"Bearer render-token")],
    }
    request = Request(scope)
    auth = await current_auth(request, session)
    with pytest.raises(ProblemError) as excinfo:
        await dependency(auth)
    assert excinfo.value.status == 403
    assert excinfo.value.slug == "insufficient_scope"


async def test_inactive_client_is_unauthenticated(session):
    """A token from an inactive client returns 401."""
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    session.add(
        ApiClient(
            tenant_id=tenant.id,
            name="inactive",
            token_hash=hash_token("inactive-token"),
            scopes=[Scope.MANAGE],
            active=False,
        )
    )
    await session.flush()

    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", b"Bearer inactive-token")],
    }
    request = Request(scope)
    with pytest.raises(ProblemError) as excinfo:
        await current_auth(request, session)
    assert excinfo.value.status == 401
    assert excinfo.value.slug == "unauthenticated"
