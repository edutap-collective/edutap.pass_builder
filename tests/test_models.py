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
            wallet_type=WalletType.APPLE,
            key="student",
            name="Student",
            is_default=True,
        )
    )
    await session.flush()
    session.add(
        TemplateVariant(
            template_id=template.id,
            wallet_type=WalletType.APPLE,
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
