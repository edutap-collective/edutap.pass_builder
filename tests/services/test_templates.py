import io
import zipfile
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from sqlmodel import SQLModel

from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.errors import ProblemError
from edutap.pass_builder.models.db import (
    CredentialSet,
    DataField,
    Template,
    TemplateVariant,
    TemplateVersion,
    Tenant,
)
from edutap.pass_builder.models.enums import (
    Provider,
    TargetKind,
    ValueType,
    VersionStatus,
    WalletType,
)
from edutap.pass_builder.services.templates import TemplateService


@pytest.fixture(autouse=True)
async def schema(session):
    await session.run_sync(lambda s: SQLModel.metadata.create_all(s.get_bind()))


class FakeObjectStore:
    """In-memory object store: records `put` calls, serves `get` from a dict."""

    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}
        self.puts: list[tuple[str, bytes, str]] = []

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        return f"{tenant}/{version_id}/{sha256}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.storage[key] = data
        self.puts.append((key, data, content_type))

    async def get(self, key: str) -> bytes:
        return self.storage[key]


@pytest.fixture
def objectstore() -> FakeObjectStore:
    return FakeObjectStore()


@dataclass
class SeededVariant:
    """A tenant/template/variant triple ready to import versions against."""

    tenant_id: UUID
    template_id: UUID
    variant_id: UUID
    variant: TemplateVariant = field(repr=False)


async def seed_variant(
    session,
    wallet_type: WalletType = WalletType.APPLE,
    *,
    key: str = "student",
    is_default: bool = True,
    credential_set_id: UUID | None = None,
) -> SeededVariant:
    """Create a tenant, template and one variant for it."""
    tenant = Tenant(key=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    session.add(tenant)
    await session.flush()

    template = Template(tenant_id=tenant.id, key="student-id", name="Student ID")
    session.add(template)
    await session.flush()

    variant = TemplateVariant(
        template_id=template.id,
        wallet_type=wallet_type,
        key=key,
        name=key,
        is_default=is_default,
        credential_set_id=credential_set_id,
    )
    session.add(variant)
    await session.flush()

    return SeededVariant(
        tenant_id=tenant.id,
        template_id=template.id,
        variant_id=variant.id,
        variant=variant,
    )


@pytest.fixture
async def tenant_variant(session) -> SeededVariant:
    return await seed_variant(session)


def make_bundle() -> bytes:
    """Build an in-memory `.pkpasstemplate`: pass.json + icon.png + tooling.json."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("pass.json", '{"formatVersion":1,"generic":{}}')
        zf.writestr("icon.png", b"\x89PNG")
        zf.writestr("tooling.json", '{"designerVersion":"1"}')
    return buffer.getvalue()


# --- import_apple_version --------------------------------------------------


async def test_apple_import_splits_pass_json_and_strips_tooling(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    version = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    assert version.pass_json["formatVersion"] == 1  # ty: ignore[not-subscriptable]

    filenames = {a.filename for a in await svc.list_assets(version.id)}
    assert "icon.png" in filenames
    assert "tooling.json" not in filenames


async def test_apple_import_content_addresses_assets_and_stores_original_bundle(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    bundle = make_bundle()
    version = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, bundle
    )

    [icon] = [a for a in await svc.list_assets(version.id) if a.filename == "icon.png"]
    assert objectstore.storage[icon.object_key] == b"\x89PNG"
    assert icon.size == len(b"\x89PNG")
    assert icon.media_type == "image/png"

    assert version.source_object_key is not None
    assert objectstore.storage[version.source_object_key] == bundle


async def test_apple_import_assigns_sequential_version_numbers(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    first = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    second = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    assert first.number == 1
    assert second.number == 2


async def test_apple_import_cross_tenant_is_not_found(
    session, objectstore, tenant_variant
):
    other_tenant = Tenant(key="other", name="Other")
    session.add(other_tenant)
    await session.flush()
    svc = TemplateService(session, objectstore)

    with pytest.raises(ProblemError) as excinfo:
        await svc.import_apple_version(
            other_tenant.id, tenant_variant.variant_id, make_bundle()
        )
    assert excinfo.value.status == 404
    assert excinfo.value.slug == "variant_not_found"


# --- set_mappings ------------------------------------------------------------


def a_rule(source_field="person.name", value_type=ValueType.TEXT) -> RuleSpec:
    return RuleSpec(
        target_kind=TargetKind.FIELD_VALUE,
        target="name",
        source_field=source_field,
        value_type=value_type,
    )


async def a_draft_apple_version(
    session, seeded: SeededVariant, status: VersionStatus = VersionStatus.DRAFT
) -> TemplateVersion:
    version = TemplateVersion(
        variant_id=seeded.variant_id,
        number=1,
        status=status,
        pass_json={"formatVersion": 1, "generic": {}},
    )
    session.add(version)
    await session.flush()
    return version


async def test_set_mappings_rejects_unknown_field(session, objectstore):
    seeded = await seed_variant(session)
    version = await a_draft_apple_version(session, seeded)
    svc = TemplateService(session, objectstore)

    with pytest.raises(ProblemError) as excinfo:
        await svc.set_mappings(seeded.tenant_id, version.id, [a_rule("person.unknown")])
    assert excinfo.value.status == 422
    assert excinfo.value.slug == "invalid_mapping"


async def test_set_mappings_stores_valid_rules(session, objectstore):
    seeded = await seed_variant(session)
    version = await a_draft_apple_version(session, seeded)
    session.add(DataField(key="person.name", value_type=ValueType.TEXT, label="Name"))
    await session.flush()
    svc = TemplateService(session, objectstore)

    await svc.set_mappings(seeded.tenant_id, version.id, [a_rule()])

    rules = await svc._rules_for(version.id)  # noqa: SLF001 - white-box check
    assert [r.source_field for r in rules] == ["person.name"]


async def test_set_mappings_on_published_version_is_409(session, objectstore):
    seeded = await seed_variant(session)
    version = await a_draft_apple_version(
        session, seeded, status=VersionStatus.PUBLISHED
    )
    svc = TemplateService(session, objectstore)

    with pytest.raises(ProblemError) as excinfo:
        await svc.set_mappings(seeded.tenant_id, version.id, [a_rule()])
    assert excinfo.value.status == 409
    assert excinfo.value.slug == "version_not_draft"


# --- publish ------------------------------------------------------------------


async def test_publish_rejects_apple_variant_with_google_payload(session, objectstore):
    seeded = await seed_variant(session, wallet_type=WalletType.APPLE)
    version = TemplateVersion(
        variant_id=seeded.variant_id,
        number=1,
        status=VersionStatus.DRAFT,
        class_json={"id": "class"},
        object_json={"id": "object"},
    )
    session.add(version)
    await session.flush()
    svc = TemplateService(session, objectstore)

    with pytest.raises(ProblemError) as excinfo:
        await svc.publish(seeded.tenant_id, version.id)
    assert excinfo.value.slug == "template_validation_failed"
    findings = excinfo.value.extra["findings"]
    assert any("pass_json" in f for f in findings)
    assert any("class_json" in f or "object_json" in f for f in findings)


async def test_publish_rejects_missing_icon(session, objectstore):
    seeded = await seed_variant(session, wallet_type=WalletType.APPLE)
    version = await a_draft_apple_version(session, seeded)
    svc = TemplateService(session, objectstore)

    with pytest.raises(ProblemError) as excinfo:
        await svc.publish(seeded.tenant_id, version.id)
    assert excinfo.value.slug == "template_validation_failed"
    assert any("icon.png" in f for f in excinfo.value.extra["findings"])


async def test_publish_succeeds_and_archives_previous_published_version(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    first = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    published_first = await svc.publish(tenant_variant.tenant_id, first.id)
    assert published_first.status == VersionStatus.PUBLISHED
    assert published_first.published_at is not None

    second = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    await svc.publish(tenant_variant.tenant_id, second.id)

    await session.refresh(first)
    assert first.status == VersionStatus.ARCHIVED


async def test_publish_requires_nfc_capable_credential_set(session, objectstore):
    tenant = Tenant(key="lmu", name="LMU")
    session.add(tenant)
    await session.flush()
    credential_set = CredentialSet(
        tenant_id=tenant.id, provider=Provider.APPLE, label="demo", nfc_capable=False
    )
    session.add(credential_set)
    await session.flush()
    template = Template(tenant_id=tenant.id, key="student-id", name="Student ID")
    session.add(template)
    await session.flush()
    variant = TemplateVariant(
        template_id=template.id,
        wallet_type=WalletType.APPLE,
        key="student",
        name="Student",
        is_default=True,
        credential_set_id=credential_set.id,
    )
    session.add(variant)
    await session.flush()

    version = TemplateVersion(
        variant_id=variant.id,
        number=1,
        status=VersionStatus.DRAFT,
        pass_json={"formatVersion": 1, "generic": {}},
        nfc_enabled=True,
    )
    session.add(version)
    await session.flush()
    svc = TemplateService(session, objectstore)
    await svc._store_asset(  # noqa: SLF001 - inject the icon without a full bundle
        tenant.id, version, "icon.png", b"\x89PNG"
    )
    await session.flush()

    with pytest.raises(ProblemError) as excinfo:
        await svc.publish(tenant.id, version.id)
    assert excinfo.value.slug == "template_validation_failed"
    assert any("nfc" in f.lower() for f in excinfo.value.extra["findings"])


async def test_publish_derives_google_rules_from_placeholders(session, objectstore):
    seeded = await seed_variant(session, wallet_type=WalletType.GOOGLE)
    version = TemplateVersion(
        variant_id=seeded.variant_id,
        number=1,
        status=VersionStatus.DRAFT,
        class_json={"id": "class"},
        object_json={
            "id": "object",
            "cardTitle": {"defaultValue": {"value": "${person.name}"}},
        },
    )
    session.add(version)
    await session.flush()
    svc = TemplateService(session, objectstore)

    published = await svc.publish(seeded.tenant_id, version.id)

    rules = await svc._rules_for(published.id)  # noqa: SLF001 - white-box check
    assert any(r.source_field == "person.name" for r in rules)


async def test_publish_on_already_published_version_is_409(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    version = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    await svc.publish(tenant_variant.tenant_id, version.id)

    with pytest.raises(ProblemError) as excinfo:
        await svc.publish(tenant_variant.tenant_id, version.id)
    assert excinfo.value.status == 409
    assert excinfo.value.slug == "version_not_draft"


# --- build_render_spec ---------------------------------------------------------


async def test_build_render_spec_resolves_default_variant_and_published_version(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    version = await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )
    await svc.publish(tenant_variant.tenant_id, version.id)

    spec = await svc.build_render_spec(
        tenant_variant.tenant_id,
        "student-id",
        WalletType.APPLE,
        variant_key=None,
        version_number=None,
    )

    assert spec.wallet_type == WalletType.APPLE
    assert spec.pass_json["formatVersion"] == 1  # ty: ignore[not-subscriptable]
    assert spec.assets["icon.png"] == b"\x89PNG"


async def test_build_render_spec_unknown_template_is_404(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    with pytest.raises(ProblemError) as excinfo:
        await svc.build_render_spec(
            tenant_variant.tenant_id,
            "no-such-template",
            WalletType.APPLE,
            variant_key=None,
            version_number=None,
        )
    assert excinfo.value.status == 404
    assert excinfo.value.slug == "template_not_found"


async def test_build_render_spec_no_published_version_is_404(
    session, objectstore, tenant_variant
):
    svc = TemplateService(session, objectstore)
    await svc.import_apple_version(
        tenant_variant.tenant_id, tenant_variant.variant_id, make_bundle()
    )  # left in draft, never published

    with pytest.raises(ProblemError) as excinfo:
        await svc.build_render_spec(
            tenant_variant.tenant_id,
            "student-id",
            WalletType.APPLE,
            variant_key=None,
            version_number=None,
        )
    assert excinfo.value.status == 404
    assert excinfo.value.slug == "version_not_found"
