import pytest
from sqlmodel import SQLModel

from edutap.pass_builder.auth import AuthContext, hash_token, resolve_token
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import ApiClient, Tenant
from edutap.pass_builder.models.enums import Scope


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


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
