import pytest
from sqlalchemy.exc import IntegrityError
from tests.dbschema import create_schema_and_tables

from edutap.pass_builder.models.db import Template, TemplateVariant, Tenant
from edutap.pass_builder.models.enums import WalletType


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: create_schema_and_tables(s.get_bind()))


async def make_template(session) -> Template:
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    template = Template(tenant_id=tenant.id, key="student-id", name="Student ID")
    session.add(template)
    await session.flush()
    return template


async def test_only_one_default_variant_per_wallet_type(session):
    template = await make_template(session)
    session.add(
        TemplateVariant(
            template_id=template.id,
            wallet_type=WalletType.APPLE_VAS,
            key="student",
            name="Student",
            is_default=True,
        )
    )
    await session.flush()
    session.add(
        TemplateVariant(
            template_id=template.id,
            wallet_type=WalletType.APPLE_VAS,
            key="staff",
            name="Staff",
            is_default=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_template_key_is_unique_per_tenant(session):
    template = await make_template(session)
    session.add(Template(tenant_id=template.tenant_id, key="student-id", name="Copy"))
    with pytest.raises(IntegrityError):
        await session.flush()


def test_tenant_active_has_a_server_default():
    """Measured on 2026-09-06, against the deployed image and a table with one row.

    `edutap-dbdef` renders DDL from this metadata -- that is the path production
    migrates on, not Alembic. With only a Python default it emitted
    `ADD COLUMN active BOOLEAN NOT NULL` and no DEFAULT, and Postgres refused it
    on the existing `lmu` row: NotNullViolation. The deploy would have stopped
    there. Alembic 0004 carried the default; the metadata did not.
    """
    column = Tenant.__table__.c.active  # ty: ignore[unresolved-attribute]
    assert column.server_default is not None, (
        "dbdef would render NOT NULL without DEFAULT"
    )
    assert column.nullable is False
