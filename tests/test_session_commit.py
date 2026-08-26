"""Prove `database.get_session`'s real commit/rollback semantics.

Every other test module in this suite drives requests through the shared,
per-test `session` fixture (see `tests/conftest.py`): a single connection
whose transaction is always rolled back at teardown, and which
`tests/routers/conftest.py` substitutes for `get_session` outright via
`app.dependency_overrides`. That gives every other test cheap isolation,
but it never exercises `get_session`'s *own* commit/rollback logic -- the
subject of this module, and of the CRITICAL production bug it was written
to catch (writes made through the real dependency were never committed at
all, so every request's effects, including its audit trail, were silently
discarded).

This module instead drives the real `get_session()` generator directly,
the same way FastAPI's dependency injection would for a single-yield
generator dependency: enter it, do work, let it exit cleanly (commit) or
raise out of it (rollback), then open a *second*, independent
`get_session()` context to check what a later request would actually see
in the database. `get_engine()` is monkeypatched to the session-scoped
`engine` fixture (the Postgres testcontainer the rest of the suite already
runs) rather than the real `Settings.database_url` -- the narrowest way to
redirect `get_session` at a real, disposable database.
"""

import base64
import os
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from tests.dbschema import create_schema_and_tables, drop_schema_and_tables

from edutap.pass_builder import database
from edutap.pass_builder.auth import AuthContext
from edutap.pass_builder.database import get_session
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import ApiClient, AuditLog, Tenant
from edutap.pass_builder.models.enums import Scope, WalletType
from edutap.pass_builder.secrets.dbcrypto import DatabaseSecretBackend
from edutap.pass_builder.services.audit import write_audit_durable
from edutap.pass_builder.services.credentials import CredentialService
from edutap.pass_builder.services.render import RenderService
from edutap.pass_builder.services.templates import TemplateService


class _Boom(Exception):
    """A distinctive failure, raised mid-request to force a rollback."""


class _FakeObjectStore:
    """Minimal in-memory object store; unused by the paths exercised here."""

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise AssertionError("not expected to be called in this module")

    async def get(self, key: str) -> bytes:
        raise AssertionError("not expected to be called in this module")


class _FakeDataProvider:
    """Placeholder; the render path here fails before any fetch happens."""

    async def fetch_fields(self, person_uid: str, fields: list[str]) -> dict:
        raise AssertionError("not expected to be called in this module")


@pytest.fixture(scope="module", autouse=True)
async def _schema(engine):
    """Create every table via a *committed* transaction, drop it afterwards.

    Unlike the rest of the suite's `schema` fixture (which creates tables
    inside the per-test, always-rolled-back `session` transaction), this
    module opens genuinely separate connections/transactions across calls
    to `get_session`, so the schema must actually be committed to be
    visible to a later, independent connection -- and is torn back down at
    the end of the module so it does not leak into the rest of the
    session-scoped `engine`/Postgres container the other test modules
    share.
    """
    async with engine.begin() as conn:
        await conn.run_sync(create_schema_and_tables)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(drop_schema_and_tables)


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch, engine):
    """Redirect the real `get_session` at the test engine, not `database_url`.

    `get_session` resolves its engine via the process-wide, `lru_cache`d
    `get_engine()`, which in production reads `Settings.database_url`.
    Patching `get_engine` itself is the narrowest way to point it at the
    Postgres testcontainer instead, without constructing a `Settings` or
    touching the cache any other test might rely on.
    """
    monkeypatch.setattr(database, "get_engine", lambda: engine)


def _real_session():
    """Return an async context manager driving `get_session()` like FastAPI does.

    `contextlib.asynccontextmanager` applied to a single-yield async
    generator function reproduces exactly how FastAPI resumes a generator
    dependency on exit -- via `.asend(None)` on a clean return, `.athrow`
    when the block raised -- so this exercises `get_session`'s commit/
    rollback branches faithfully, not a hand-rolled approximation of them.
    """
    return asynccontextmanager(get_session)()


# --- get_session: commit on success, rollback on exception -------------------


async def test_commit_persists_across_separate_get_session_calls():
    """A Tenant added in one `get_session` context is visible in the next.

    This is the core of the CRITICAL fix: before it, `get_session` never
    called `commit()`, so this Tenant would vanish the moment the first
    `async with` block exited -- exactly what happened to every
    template/credential/publish/sync write in production.
    """
    key = f"commit-{uuid.uuid4().hex}"
    async with _real_session() as session:
        session.add(Tenant(key=key, name="Commit Tenant"))

    async with _real_session() as session:
        result = await session.execute(
            select(Tenant).where(
                Tenant.key  # ty: ignore[invalid-argument-type]
                == key
            )
        )
        assert result.scalar_one_or_none() is not None


async def test_rollback_discards_writes_when_the_block_raises():
    """A Tenant added in a `get_session` context that raises is never persisted."""
    key = f"rollback-{uuid.uuid4().hex}"

    with pytest.raises(_Boom):
        async with _real_session() as session:
            session.add(Tenant(key=key, name="Rollback Tenant"))
            await session.flush()
            raise _Boom("simulated failure mid-request")

    async with _real_session() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.key == key)  # ty: ignore[invalid-argument-type]
        )
        assert result.scalar_one_or_none() is None


# --- write_audit_durable: survives the rollback that discards everything else -


async def test_durable_audit_survives_rollback_but_the_partial_write_does_not():
    """`write_audit_durable`'s row outlives a rollback; a plain write does not.

    Mirrors what `RenderService._write_error_audit` and
    `routers/_lifecycle_audit.py::audited` rely on: within one failed
    request (one `get_session` context that raises), an error-audit entry
    written through `write_audit_durable` must still be there afterwards,
    while an ordinary write made in the same, now-rolled-back session must
    not be -- proving the mechanism keeps the audit trail without also
    keeping the failed operation's partial data.
    """
    async with _real_session() as session:
        tenant = Tenant(key=f"partial-{uuid.uuid4().hex}", name="Partial Tenant")
        session.add(tenant)
        await session.flush()
        # A committed, pre-existing actor -- the audit entry's FK -- distinct
        # from the operation's own (about-to-be-discarded) partial write
        # below, mirroring how a real request's `auth.client_id` always
        # already exists before that request's own writes ever happen.
        actor = ApiClient(
            tenant_id=tenant.id,
            name="actor-client",
            token_hash=f"unused-{uuid.uuid4().hex}",  # noqa: S106 - test fixture
            scopes=[],
        )
        session.add(actor)
        await session.flush()
        tenant_id, actor_id = tenant.id, actor.id

    request_id = f"req-{uuid.uuid4().hex}"
    with pytest.raises(_Boom):
        async with _real_session() as session:
            # The (would-be) operation's own partial write -- must not survive.
            client = ApiClient(
                tenant_id=tenant_id,
                name="partial-client",
                token_hash=f"unused-{uuid.uuid4().hex}",  # noqa: S106 - test fixture
                scopes=[],
            )
            session.add(client)
            await session.flush()

            # The error-audit entry -- must survive.
            await write_audit_durable(
                session,
                tenant_id=tenant_id,
                request_id=request_id,
                actor_client_id=actor_id,
                action="pass.create",
                outcome="error",
                error_code="internal_error",
                duration_ms=1,
                template_id=None,
                variant_id=None,
                version_id=None,
                wallet_type=None,
                subject_ref="u1",
                requested_fields=[],
            )
            raise _Boom("simulated failure after the audit write")

    async with _real_session() as session:
        client_result = await session.execute(
            select(ApiClient).where(
                ApiClient.name == "partial-client"  # ty: ignore[invalid-argument-type]
            )
        )
        assert client_result.scalar_one_or_none() is None, (
            "the failed operation's own partial write must not persist"
        )

        audit_result = await session.execute(
            select(AuditLog).where(
                AuditLog.request_id == request_id  # ty: ignore[invalid-argument-type]
            )
        )
        entry = audit_result.scalar_one_or_none()
        assert entry is not None, "the error audit entry must survive the rollback"
        assert entry.outcome == "error"
        assert entry.error_code == "internal_error"


# --- end-to-end: RenderService through the real commit path ------------------


async def test_render_service_error_audit_survives_the_real_rollback():
    """`RenderService.create_pass`'s error audit survives a real rollback.

    End-to-end version of the previous test, through the actual method
    that relies on this behaviour in production: a `template_not_found`
    failure raised inside a real, non-rolled-back `get_session` context
    still leaves its `outcome="error"` audit row behind once that
    context's rollback runs and the `ProblemError` propagates out.
    """
    async with _real_session() as session:
        tenant = Tenant(key=f"render-{uuid.uuid4().hex}", name="Render Tenant")
        session.add(tenant)
        await session.flush()
        api_client = ApiClient(
            tenant_id=tenant.id,
            name="renderer",
            token_hash=f"unused-{uuid.uuid4().hex}",  # noqa: S106 - test fixture
            scopes=[Scope.RENDER],
        )
        session.add(api_client)
        await session.flush()
        tenant_id, client_id = tenant.id, api_client.id

    request_id = f"req-{uuid.uuid4().hex}"
    backend = DatabaseSecretBackend(base64.b64encode(os.urandom(32)).decode())

    with pytest.raises(ProblemError) as excinfo:
        async with _real_session() as session:
            templates = TemplateService(session, _FakeObjectStore())
            credentials = CredentialService(session, backend)
            service = RenderService(
                session,
                templates,
                credentials,
                _FakeDataProvider(),  # ty: ignore[invalid-argument-type]
            )
            auth = AuthContext(
                client_id=client_id, tenant_id=tenant_id, scopes={Scope.RENDER}
            )
            await service.create_pass(
                auth,
                pass_id="1",  # noqa: S106 - pass_id is an identifier, not a secret
                template_key="no-such-template",
                wallet_type=WalletType.APPLE,
                variant_key=None,
                person_uid="u1",
                version_number=None,
                request_id=request_id,
            )
    assert excinfo.value.slug == "template_not_found"

    async with _real_session() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.request_id  # ty: ignore[invalid-argument-type]
                == request_id
            )
        )
        entry = result.scalar_one_or_none()
        assert entry is not None
        assert entry.outcome == "error"
        assert entry.error_code == "template_not_found"
        assert entry.template_id is None
