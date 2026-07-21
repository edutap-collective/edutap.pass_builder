"""Template lifecycle: Apple import, mapping rules, publish, render spec."""

import hashlib
import io
import json
import mimetypes
import zipfile
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..engine.placeholders import scan_placeholders
from ..engine.spec import RenderSpec, RuleSpec
from ..errors import ProblemError
from ..models.db import (
    CredentialSet,
    DataField,
    MappingRule,
    Template,
    TemplateAsset,
    TemplateVariant,
    TemplateVersion,
)
from ..models.enums import RuleOrigin, TargetKind, ValueType, VersionStatus, WalletType
from .mapping_validation import validate_mapping_rules

_TOOLING_JSON = "tooling.json"
_PASS_JSON = "pass.json"  # noqa: S105 - a filename, not a credential
_APPLE_ICON = "icon.png"

# Mirrors `_FIELD_GROUPS`/`_STYLES` in `engine/apple_apply.py`: the field
# groups and pass style blocks Apple's pass.json can hold. Kept as a local
# copy rather than importing those (module-private) names across modules.
_APPLE_FIELD_GROUPS = (
    "headerFields",
    "primaryFields",
    "secondaryFields",
    "auxiliaryFields",
    "backFields",
)
_APPLE_STYLES = ("boardingPass", "coupon", "eventTicket", "generic", "storeCard")

_FIELD_TARGET_KINDS = (TargetKind.FIELD_VALUE, TargetKind.FIELD_LABEL)


class SupportsObjectStore(Protocol):
    """The subset of `ObjectStore` this service depends on.

    A `Protocol` rather than the concrete class so unit tests can inject an
    in-memory fake without hitting real object storage.
    """

    @staticmethod
    def content_key(tenant: str, version_id: str, sha256: str) -> str:
        """Return the content-addressed object key."""
        ...

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store a blob under the given key."""
        ...

    async def get(self, key: str) -> bytes:
        """Retrieve the blob stored under the given key."""
        ...


def _apple_field_keys(pass_json: dict) -> set[str]:
    """Return every field `key` found across an Apple pass.json's field groups.

    Walks every pass style block (`boardingPass`, `coupon`, `eventTicket`,
    `generic`, `storeCard`) and every standard field group within it
    (`headerFields`, `primaryFields`, `secondaryFields`, `auxiliaryFields`,
    `backFields`), collecting the `key` of each field entry.
    """
    keys: set[str] = set()
    for style in _APPLE_STYLES:
        style_block = pass_json.get(style)
        if not isinstance(style_block, dict):
            continue
        for group in _APPLE_FIELD_GROUPS:
            for field in style_block.get(group, []):
                key = field.get("key")
                if key is not None:
                    keys.add(key)
    return keys


def _mapping_rule_to_spec(row: MappingRule) -> RuleSpec:
    """Convert a persisted `MappingRule` row into an engine `RuleSpec`."""
    return RuleSpec(
        target_kind=row.target_kind,
        target=row.target,
        source_field=row.source_field,
        value_type=row.value_type,
        required=row.required,
        default_value=row.default_value,
        position=row.position,
    )


class TemplateService:
    """Imports, validates and publishes template versions."""

    def __init__(self, session: AsyncSession, objectstore: SupportsObjectStore) -> None:
        """Bind the service to one session and one object store."""
        self._session = session
        self._objectstore = objectstore

    async def import_apple_version(
        self, tenant_id: UUID, variant_id: UUID, bundle: bytes
    ) -> TemplateVersion:
        """Import an Apple `.pkpasstemplate` bundle as a new draft version.

        Splits `pass.json` into the version's `pass_json` column, stores
        every other file (except `tooling.json`, which carries no rendering
        information) as a content-addressed `TemplateAsset`, and keeps the
        untouched original bundle under `source_object_key`.
        """
        variant = await self._load_variant(tenant_id, variant_id)
        number = await self._next_version_number(variant.id)

        version = TemplateVersion(
            variant_id=variant.id, number=number, status=VersionStatus.DRAFT
        )
        self._session.add(version)
        await self._session.flush()

        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if _PASS_JSON not in names:
                raise ProblemError(
                    422, "missing_pass_json", "Bundle has no pass.json entry"
                )
            version.pass_json = json.loads(archive.read(_PASS_JSON).decode("utf-8"))

            for name in names:
                if name in (_PASS_JSON, _TOOLING_JSON):
                    continue
                data = archive.read(name)
                await self._store_asset(tenant_id, version, name, data)

        bundle_sha256 = hashlib.sha256(bundle).hexdigest()
        version.source_object_key = self._objectstore.content_key(
            str(tenant_id), str(version.id), bundle_sha256
        )
        await self._objectstore.put(
            version.source_object_key, bundle, "application/zip"
        )

        self._session.add(version)
        await self._session.flush()
        return version

    async def list_assets(self, version_id: UUID) -> list[TemplateAsset]:
        """Return the assets stored for a version.

        Not tenant-scoped by itself -- callers that expose this across a
        tenant boundary must first resolve the version through
        `_load_version`, which does enforce tenant ownership.
        """
        query = select(TemplateAsset).where(
            TemplateAsset.version_id  # ty: ignore[invalid-argument-type]
            == version_id
        )
        return list((await self._session.execute(query)).scalars().all())

    async def set_mappings(
        self, tenant_id: UUID, version_id: UUID, rules: list[RuleSpec]
    ) -> None:
        """Replace a draft version's mapping rules.

        Raises `ProblemError(409, "version_not_draft")` once the version has
        left draft status, and `ProblemError(422, "invalid_mapping")` if any
        rule fails validation against the cached data-provider catalogue.
        """
        version = await self._load_version(tenant_id, version_id)
        if version.status != VersionStatus.DRAFT:
            raise ProblemError(
                409, "version_not_draft", "Only draft versions accept mapping changes"
            )

        catalogue = await self._data_field_catalogue()
        problems = validate_mapping_rules(rules, catalogue)
        if problems:
            raise ProblemError(
                422,
                "invalid_mapping",
                "One or more mapping rules are invalid",
                findings=problems,
            )

        await self._replace_rules(version_id, rules, origin=RuleOrigin.AUTHORED)
        await self._session.flush()

    async def publish(self, tenant_id: UUID, version_id: UUID) -> TemplateVersion:
        """Publish a draft version after running full validation.

        Collects every finding before raising, so callers see the complete
        set of problems in one `ProblemError(422, "template_validation_failed")`
        rather than one at a time. On success, derives Google mapping rules
        from `${...}` placeholders in `object_json`, marks the version
        `published`, and archives the variant's previously published
        version.
        """
        version = await self._load_version(tenant_id, version_id)
        if version.status != VersionStatus.DRAFT:
            raise ProblemError(
                409, "version_not_draft", "Only draft versions can be published"
            )
        variant = await self._session.get(TemplateVariant, version.variant_id)
        assert variant is not None  # noqa: S101 - FK guarantees existence

        problems = await self._validate_for_publish(tenant_id, version, variant)
        if problems:
            raise ProblemError(
                422,
                "template_validation_failed",
                "Template version failed publish validation",
                findings=problems,
            )

        if variant.wallet_type == WalletType.GOOGLE:
            derived = [
                RuleSpec(
                    target_kind=TargetKind.JSON_POINTER,
                    target=pointer,
                    source_field=source_field,
                    value_type=ValueType.TEXT,
                    required=False,
                    position=position,
                )
                for position, (pointer, source_field) in enumerate(
                    scan_placeholders(version.object_json)
                )
            ]
            await self._replace_rules(version_id, derived, origin=RuleOrigin.DERIVED)

        # Flushed separately: the partial unique index on `status =
        # 'published'` is checked per statement (Postgres does not defer
        # plain unique indexes), so the old row must actually leave
        # `published` before the new row can enter it in the same flush.
        await self._archive_published(variant.id)
        await self._session.flush()

        version.status = VersionStatus.PUBLISHED
        version.published_at = datetime.now(UTC)
        self._session.add(version)
        await self._session.flush()
        return version

    async def build_render_spec(
        self,
        tenant_id: UUID,
        template_key: str,
        wallet_type: WalletType,
        variant_key: str | None,
        version_number: int | None,
    ) -> RenderSpec:
        """Resolve a template/variant/version and assemble a `RenderSpec`.

        `variant_key` picks a specific variant, otherwise the wallet type's
        default variant is used. `version_number` pins a specific version,
        otherwise the currently published one is used.
        """
        template = await self._load_template(tenant_id, template_key)
        variant = await self._resolve_variant(template.id, wallet_type, variant_key)
        version = await self._resolve_version(variant.id, version_number)

        assets: dict[str, bytes] = {}
        for asset in await self.list_assets(version.id):
            assets[asset.filename] = await self._objectstore.get(asset.object_key)

        rules = [
            _mapping_rule_to_spec(row) for row in await self._rules_for(version.id)
        ]

        issuer_id: str | None = None
        if wallet_type == WalletType.GOOGLE and variant.credential_set_id is not None:
            credential_set = await self._load_credential_set(
                tenant_id, variant.credential_set_id
            )
            if credential_set is not None:
                issuer_id = credential_set.issuer_id

        return RenderSpec(
            wallet_type=wallet_type,
            pass_json=version.pass_json,
            class_json=version.class_json,
            object_json=version.object_json,
            assets=assets,
            rules=rules,
            nfc_enabled=version.nfc_enabled,
            nfc_encryption_public_key=version.nfc_encryption_public_key,
            nfc_requires_authentication=version.nfc_requires_authentication,
            issuer_id=issuer_id,
        )

    async def _store_asset(
        self, tenant_id: UUID, version: TemplateVersion, name: str, data: bytes
    ) -> None:
        """Content-address one bundle entry and persist it as an asset."""
        sha256 = hashlib.sha256(data).hexdigest()
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        key = self._objectstore.content_key(str(tenant_id), str(version.id), sha256)
        await self._objectstore.put(key, data, media_type)
        self._session.add(
            TemplateAsset(
                version_id=version.id,
                filename=name,
                media_type=media_type,
                size=len(data),
                sha256=sha256,
                object_key=key,
            )
        )

    async def _validate_for_publish(
        self, tenant_id: UUID, version: TemplateVersion, variant: TemplateVariant
    ) -> list[str]:
        """Run every publish-time check, returning all findings at once."""
        problems: list[str] = []
        problems.extend(self._check_platform_payload(version, variant))

        catalogue = await self._data_field_catalogue()
        authored_rules = [
            _mapping_rule_to_spec(row) for row in await self._rules_for(version.id)
        ]
        problems.extend(validate_mapping_rules(authored_rules, catalogue))

        if variant.wallet_type == WalletType.APPLE:
            filenames = {asset.filename for asset in await self.list_assets(version.id)}
            if _APPLE_ICON not in filenames:
                problems.append(f"missing required asset: {_APPLE_ICON}")
            problems.extend(self._check_apple_mapping_targets(version, authored_rules))

        if version.nfc_enabled:
            problems.extend(await self._check_nfc_capable(tenant_id, variant))

        return problems

    def _check_platform_payload(
        self, version: TemplateVersion, variant: TemplateVariant
    ) -> list[str]:
        """Check the version carries exactly the payload its platform needs.

        Mirrors the DB's `ck_template_version_platform_payload` CHECK, which
        can only require "at least one payload" since a row cannot see its
        variant's `wallet_type`. This adds the platform-specific half: Apple
        variants must have `pass_json` and nothing Google-shaped; Google
        variants must have both `class_json` and `object_json` and no
        `pass_json`.
        """
        problems: list[str] = []
        if variant.wallet_type == WalletType.APPLE:
            if version.pass_json is None:
                problems.append("apple variant requires pass_json to be set")
            if version.class_json is not None or version.object_json is not None:
                problems.append("apple variant must not set class_json or object_json")
        elif variant.wallet_type == WalletType.GOOGLE:
            if version.class_json is None or version.object_json is None:
                problems.append(
                    "google variant requires both class_json and object_json"
                )
            if version.pass_json is not None:
                problems.append("google variant must not set pass_json")
        return problems

    def _check_apple_mapping_targets(
        self, version: TemplateVersion, rules: list[RuleSpec]
    ) -> list[str]:
        """Check that every field-targeting rule points at a real pass field.

        A `field_value`/`field_label` rule whose `target` is not a field
        `key` anywhere in `pass_json` would silently render a blank field,
        so it is a publish-time error rather than a runtime surprise.
        """
        if version.pass_json is None:
            return []
        field_keys = _apple_field_keys(version.pass_json)
        return [
            f"mapping target field not found in pass: {rule.target}"
            for rule in rules
            if rule.target_kind in _FIELD_TARGET_KINDS and rule.target not in field_keys
        ]

    async def _check_nfc_capable(
        self, tenant_id: UUID, variant: TemplateVariant
    ) -> list[str]:
        """Check the variant's credential set supports NFC when required."""
        credential_set = None
        if variant.credential_set_id is not None:
            credential_set = await self._load_credential_set(
                tenant_id, variant.credential_set_id
            )
        if credential_set is None or not credential_set.nfc_capable:
            return ["nfc_enabled requires an nfc_capable credential set"]
        return []

    async def _load_credential_set(
        self, tenant_id: UUID, credential_set_id: UUID
    ) -> CredentialSet | None:
        """Return the tenant-scoped credential set for an id, or `None` if absent.

        Tenant-scoped so a variant's `credential_set_id` can never resolve
        another tenant's credential set here -- defense in depth alongside
        the render path's own tenant-scoped lookup.
        """
        query = select(CredentialSet).where(
            CredentialSet.id  # ty: ignore[invalid-argument-type]
            == credential_set_id,
            CredentialSet.tenant_id  # ty: ignore[invalid-argument-type]
            == tenant_id,
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def _replace_rules(
        self, version_id: UUID, rules: list[RuleSpec], *, origin: RuleOrigin
    ) -> None:
        """Replace a version's mapping rules of one origin with new ones."""
        query = select(MappingRule).where(
            MappingRule.version_id  # ty: ignore[invalid-argument-type]
            == version_id,
            MappingRule.origin  # ty: ignore[invalid-argument-type]
            == origin,
        )
        for row in (await self._session.execute(query)).scalars().all():
            await self._session.delete(row)
        await self._session.flush()

        for rule in rules:
            self._session.add(
                MappingRule(
                    version_id=version_id,
                    origin=origin,
                    target_kind=rule.target_kind,
                    target=rule.target,
                    source_field=rule.source_field,
                    value_type=rule.value_type,
                    required=rule.required,
                    default_value=rule.default_value,
                    position=rule.position,
                )
            )

    async def _archive_published(self, variant_id: UUID) -> None:
        """Archive the variant's currently published version, if any."""
        query = select(TemplateVersion).where(
            TemplateVersion.variant_id  # ty: ignore[invalid-argument-type]
            == variant_id,
            TemplateVersion.status  # ty: ignore[invalid-argument-type]
            == VersionStatus.PUBLISHED,
        )
        previous = (await self._session.execute(query)).scalar_one_or_none()
        if previous is not None:
            previous.status = VersionStatus.ARCHIVED
            self._session.add(previous)

    async def _rules_for(self, version_id: UUID) -> list[MappingRule]:
        """Return a version's mapping rules ordered for stable rendering."""
        query = (
            select(MappingRule)
            .where(
                MappingRule.version_id  # ty: ignore[invalid-argument-type]
                == version_id
            )
            .order_by(MappingRule.position)  # ty: ignore[invalid-argument-type]
        )
        return list((await self._session.execute(query)).scalars().all())

    async def _data_field_catalogue(self) -> dict[str, str]:
        """Load the cached data-provider field catalogue as key -> value type."""
        rows = (await self._session.execute(select(DataField))).scalars().all()
        return {row.key: row.value_type.value for row in rows}

    async def _next_version_number(self, variant_id: UUID) -> int:
        """Return the next sequential version number for a variant."""
        query = select(func.max(TemplateVersion.number)).where(
            TemplateVersion.variant_id  # ty: ignore[invalid-argument-type]
            == variant_id
        )
        current_max = (await self._session.execute(query)).scalar_one()
        return (current_max or 0) + 1

    async def _load_template(self, tenant_id: UUID, template_key: str) -> Template:
        """Return the tenant-scoped template or raise 404."""
        query = select(Template).where(
            Template.tenant_id  # ty: ignore[invalid-argument-type]
            == tenant_id,
            Template.key  # ty: ignore[invalid-argument-type]
            == template_key,
        )
        template = (await self._session.execute(query)).scalar_one_or_none()
        if template is None:
            raise ProblemError(
                404, "template_not_found", "No such template for this tenant"
            )
        return template

    async def _resolve_variant(
        self, template_id: UUID, wallet_type: WalletType, variant_key: str | None
    ) -> TemplateVariant:
        """Return a specific or the default variant for one wallet type."""
        query = select(TemplateVariant).where(
            TemplateVariant.template_id  # ty: ignore[invalid-argument-type]
            == template_id,
            TemplateVariant.wallet_type  # ty: ignore[invalid-argument-type]
            == wallet_type,
        )
        if variant_key is not None:
            query = query.where(
                TemplateVariant.key  # ty: ignore[invalid-argument-type]
                == variant_key
            )
        else:
            query = query.where(
                TemplateVariant.is_default.is_(True)  # ty: ignore[unresolved-attribute]
            )
        variant = (await self._session.execute(query)).scalar_one_or_none()
        if variant is None:
            raise ProblemError(404, "variant_not_found", "No such template variant")
        return variant

    async def _resolve_version(
        self, variant_id: UUID, version_number: int | None
    ) -> TemplateVersion:
        """Return a specific version, or the published one, for a variant."""
        query = select(TemplateVersion).where(
            TemplateVersion.variant_id  # ty: ignore[invalid-argument-type]
            == variant_id
        )
        if version_number is not None:
            query = query.where(
                TemplateVersion.number  # ty: ignore[invalid-argument-type]
                == version_number
            )
        else:
            query = query.where(
                TemplateVersion.status  # ty: ignore[invalid-argument-type]
                == VersionStatus.PUBLISHED
            )
        version = (await self._session.execute(query)).scalar_one_or_none()
        if version is None:
            raise ProblemError(404, "version_not_found", "No such template version")
        return version

    async def _load_variant(self, tenant_id: UUID, variant_id: UUID) -> TemplateVariant:
        """Return the tenant-scoped variant or raise 404."""
        query = (
            select(TemplateVariant)
            .join(
                Template,
                TemplateVariant.template_id  # ty: ignore[invalid-argument-type]
                == Template.id,
            )
            .where(
                TemplateVariant.id  # ty: ignore[invalid-argument-type]
                == variant_id,
                Template.tenant_id  # ty: ignore[invalid-argument-type]
                == tenant_id,
            )
        )
        variant = (await self._session.execute(query)).scalar_one_or_none()
        if variant is None:
            raise ProblemError(
                404, "variant_not_found", "No such template variant for this tenant"
            )
        return variant

    async def _load_version(self, tenant_id: UUID, version_id: UUID) -> TemplateVersion:
        """Return the tenant-scoped version or raise 404."""
        query = (
            select(TemplateVersion)
            .join(
                TemplateVariant,
                TemplateVersion.variant_id  # ty: ignore[invalid-argument-type]
                == TemplateVariant.id,
            )
            .join(
                Template,
                TemplateVariant.template_id  # ty: ignore[invalid-argument-type]
                == Template.id,
            )
            .where(
                TemplateVersion.id  # ty: ignore[invalid-argument-type]
                == version_id,
                Template.tenant_id  # ty: ignore[invalid-argument-type]
                == tenant_id,
            )
        )
        version = (await self._session.execute(query)).scalar_one_or_none()
        if version is None:
            raise ProblemError(
                404, "version_not_found", "No such template version for this tenant"
            )
        return version
