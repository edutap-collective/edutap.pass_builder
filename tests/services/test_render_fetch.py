"""The delivery path: rebuild an issued Apple pass from Apple's key alone.

`wallet_apple_vas_web_service` holds registrations and knows no person, no
template and no validity. It asks with the only two values Apple gives it, and
this service recovers the rest from `public.pass_state` -- a table it reads and
never writes.
"""

from datetime import UTC, datetime

import pytest
from edutap.data_models.vocabulary import HolderState, IssuanceState
from edutap.db_definitions.public.tables import PassState
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.auth import AuthContext
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import CredentialSet, TemplateVariant
from edutap.pass_builder.models.enums import (
    CredentialStatus,
    Provider,
    Scope,
    WalletType,
)

from .test_render import (
    FakeDataProvider,
    FakeObjectStore,
    _make_service,
    _seed_published_apple_template,
    _seed_tenant_and_client,
)


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


PASS_TYPE = "pass.test.example"  # noqa: S105 - an Apple identifier, not a secret
SERIAL = "11111111-1111-1111-1111-111111111111"


async def _credentialled_variant(session, tenant_id) -> TemplateVariant:
    """The seeded Apple variant, with a credential set that names the pass type.

    A row rather than a real certificate: `_require_pass_type` reads the
    parsed identifier, and the signer is replaced in these tests anyway.
    """
    variant = await _seed_published_apple_template(session, tenant_id)
    credential_set = CredentialSet(
        tenant_id=tenant_id,
        provider=Provider.APPLE,
        label="apple-demo",
        status=CredentialStatus.ACTIVE,
        pass_type_identifier=PASS_TYPE,
    )
    session.add(credential_set)
    await session.flush()
    variant.credential_set_id = credential_set.id
    session.add(variant)
    await session.flush()
    return variant


async def _issue(session, *, person_uid="u1", state=IssuanceState.ISSUED, **overrides):
    """Write the row the pass-state consumer would have written."""
    row = PassState(
        pass_id=overrides.get("pass_id", SERIAL),
        person_uid=person_uid,
        wallet_type=overrides.get("wallet_type", WalletType.APPLE_VAS),
        issuance_state=state,
        holder_state=HolderState.NOT_PRESENT,
        pass_template=overrides.get("pass_template", "student-id"),
        pass_template_variant=overrides.get("pass_template_variant", "student"),
        # The consumer's high-water mark. `NOT NULL` there is what stops a row
        # from arriving without one, so a fixture has to supply it too.
        last_event_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


@pytest.fixture
async def fetch_env(session):
    tenant, api_client = await _seed_tenant_and_client(session)
    await _credentialled_variant(session, tenant.id)
    service = _make_service(
        session, FakeObjectStore(), FakeDataProvider(response={"person.name": "Ada"})
    )
    auth = AuthContext(
        client_id=api_client.id, tenant_id=tenant.id, scopes={Scope.RENDER}
    )
    return session, service, auth


async def test_an_issued_pass_is_rebuilt_from_its_serial_number(fetch_env):
    """The whole point: Apple's key in, a current `.pkpass` out."""
    session, service, auth = fetch_env
    await _issue(session)

    result = await service.fetch_apple_pass(
        auth, pass_type_identifier=PASS_TYPE, serial_number=SERIAL
    )

    assert result.pkpass is not None
    assert result.wallet_type == WalletType.APPLE_VAS


async def test_the_pass_is_rebuilt_rather_than_read_from_store(fetch_env):
    """The data provider is asked every time.

    A stored copy would be a second truth with an unbounded staleness, and a
    personal pass sitting at rest. That the fields are fetched on each call is
    what makes "the current pass" true rather than aspirational.
    """
    session, service, auth = fetch_env
    await _issue(session)

    await service.fetch_apple_pass(
        auth, pass_type_identifier=PASS_TYPE, serial_number=SERIAL
    )

    assert service._data_provider.last_fields == ["person.name"]  # noqa: SLF001


async def test_an_unknown_serial_number_is_404(fetch_env):
    _session, service, auth = fetch_env
    with pytest.raises(ProblemError) as excinfo:
        await service.fetch_apple_pass(
            auth, pass_type_identifier=PASS_TYPE, serial_number="nope"
        )
    assert excinfo.value.status == 404
    assert excinfo.value.slug == "pass_not_found"


async def test_a_withdrawn_pass_is_410_not_404(fetch_env):
    """A device asking for a revoked pass should stop asking.

    A `404` invites a retry; `410` says the pass existed and is gone.
    """
    session, service, auth = fetch_env
    await _issue(session, state=IssuanceState.REVOKED)

    with pytest.raises(ProblemError) as excinfo:
        await service.fetch_apple_pass(
            auth, pass_type_identifier=PASS_TYPE, serial_number=SERIAL
        )
    assert excinfo.value.status == 410
    assert excinfo.value.slug == "pass_revoked"


async def test_a_google_pass_is_not_deliverable_here(fetch_env):
    """This route delivers `.pkpass` bytes; a Google object has none."""
    session, service, auth = fetch_env
    await _issue(session, wallet_type=WalletType.GOOGLE_ST)

    with pytest.raises(ProblemError) as excinfo:
        await service.fetch_apple_pass(
            auth, pass_type_identifier=PASS_TYPE, serial_number=SERIAL
        )
    assert excinfo.value.status == 404


async def test_the_wrong_pass_type_answers_like_an_unknown_serial(fetch_env):
    """404 and not 403, and the same slug as a serial that does not exist.

    Otherwise the difference between the two answers tells a caller which
    serial numbers exist under a pass type it is not asking about.
    """
    session, service, auth = fetch_env
    await _issue(session)

    with pytest.raises(ProblemError) as excinfo:
        await service.fetch_apple_pass(
            auth,
            pass_type_identifier="pass.someone.else",  # noqa: S106 - an identifier
            serial_number=SERIAL,
        )
    assert excinfo.value.status == 404
    assert excinfo.value.slug == "pass_not_found"


async def test_a_fetch_is_audited_as_such(fetch_env):
    """`pass.fetch`, distinct from `pass.create`.

    A delivery is not an issuance, and an audit that cannot tell them apart
    cannot answer how often a device came back.
    """
    session, service, auth = fetch_env
    await _issue(session)

    await service.fetch_apple_pass(
        auth, pass_type_identifier=PASS_TYPE, serial_number=SERIAL
    )

    from sqlalchemy import select

    from edutap.pass_builder.models.db import AuditLog

    entries = (await session.execute(select(AuditLog))).scalars().all()
    assert [entry.action for entry in entries] == ["pass.fetch"]
    assert entries[0].outcome == "success"


async def test_another_tenants_pass_is_not_deliverable(session):
    """`pass_state` is not tenant-scoped -- the template lookup is.

    The row names a template *key*, and resolving that key happens inside the
    asking tenant. A serial number belonging to another tenant therefore fails
    at the template, not at the row.
    """
    tenant_a, client_a = await _seed_tenant_and_client(session)
    await _credentialled_variant(session, tenant_a.id)
    await _issue(
        session,
        pass_template="not-a-template-of-this-tenant",  # noqa: S106 - a key
    )

    service = _make_service(
        session, FakeObjectStore(), FakeDataProvider(response={"person.name": "Ada"})
    )
    auth = AuthContext(
        client_id=client_a.id, tenant_id=tenant_a.id, scopes={Scope.RENDER}
    )

    with pytest.raises(ProblemError) as excinfo:
        await service.fetch_apple_pass(
            auth, pass_type_identifier=PASS_TYPE, serial_number=SERIAL
        )
    assert excinfo.value.status == 404
