"""SQLModel table definitions."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import Base
from .enums import (
    CredentialStatus,
    Provider,
    RuleOrigin,
    SecretKind,
    TargetKind,
    ValueType,
    VersionStatus,
    WalletType,
)

# Every timestamp is `timestamptz` (spec section 3). SQLModel's default
# mapping for `datetime` is a naive `TIMESTAMP WITHOUT TIME ZONE`, which
# rejects the timezone-aware values `datetime.now(UTC)` produces, so every
# datetime field below goes through `_tz()`, sharing one timezone-aware type.
_TZ_DATETIME = DateTime(timezone=True)


def _now() -> datetime:
    """Return the current time, timezone-aware, for `created_at`-style defaults."""
    return datetime.now(UTC)


def _tz(*, nullable: bool, index: bool = False) -> Column:
    """Build one `timestamptz` column.

    `Field(sa_type=...)` only accepts a *type class*, not a configured
    *instance* -- `DateTime(timezone=True)` is inherently an instance, so
    the column goes through `sa_column` instead. That bypasses SQLModel's
    own nullability inference from the `X | None` annotation, hence the
    explicit `nullable` here.
    """
    return Column(_TZ_DATETIME, nullable=nullable, index=index)


def _enum_type(enum_cls: type[Enum], name: str) -> SqlEnum:
    """Build a native Postgres enum type storing member *values*, not names.

    SQLAlchemy's default behaviour for a Python ``Enum`` column is to persist
    the member *name* (e.g. ``"PUBLISHED"``), not its ``.value`` (e.g.
    ``"published"``). Our enums use lower_snake_case values matching the
    spec's slugs, so every enum-typed column needs ``values_callable`` to
    persist those values instead -- otherwise raw-SQL predicates such as the
    partial unique index ``WHERE status = 'published'`` would never match.
    One shared instance per enum class/name pair is reused across tables so
    the underlying Postgres ``CREATE TYPE`` only happens once per name.

    ``inherit_schema=True`` puts the type in the schema of the table that uses
    it. SQLAlchemy scopes a type to the *metadata*, not to that table, so
    without it the ``CREATE TYPE`` lands wherever ``search_path`` resolves --
    typically ``public``, the one schema reserved for the cross-package
    contract. `edutap-dbdef` reports that as ``unqualified_type`` and refuses
    to run rather than create the type in a namespace every package shares.
    """
    return SqlEnum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        name=name,
        inherit_schema=True,
    )


def _enum_column(enum_type: SqlEnum, *, nullable: bool) -> Column:
    """Wrap a shared enum type in a fresh `Column` (see `_tz` for why)."""
    return Column(enum_type, nullable=nullable)


_wallet_type = _enum_type(WalletType, "wallet_type")
_value_type = _enum_type(ValueType, "value_type")


class Tenant(Base, table=True):
    """An organisational unit owning templates and credentials."""

    __tablename__ = "tenant"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(unique=True, index=True)
    name: str
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))


class ApiClient(Base, table=True):
    """A machine credential authenticating requests for exactly one tenant."""

    __tablename__ = "api_client"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenant.id", index=True)
    name: str
    token_hash: str = Field(unique=True, index=True)
    scopes: list[str] = Field(sa_column=Column(ARRAY(String)))
    active: bool = True
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))
    last_used_at: datetime | None = Field(default=None, sa_column=_tz(nullable=True))


class CredentialSet(Base, table=True):
    """A signing credential for one wallet provider, one renewal chain."""

    __tablename__ = "credential_set"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenant.id", index=True)
    provider: Provider = Field(
        sa_column=_enum_column(_enum_type(Provider, "provider"), nullable=False)
    )
    label: str
    status: CredentialStatus = Field(
        default=CredentialStatus.KEY_PENDING,
        sa_column=_enum_column(
            _enum_type(CredentialStatus, "credential_status"), nullable=False
        ),
    )
    predecessor_id: UUID | None = Field(default=None, foreign_key="credential_set.id")

    # Apple, derived from the certificate, never typed in.
    pass_type_identifier: str | None = None
    team_identifier: str | None = None
    organization_name: str | None = None
    cert_serial: str | None = None
    cert_fingerprint_sha256: str | None = None
    not_before: datetime | None = Field(default=None, sa_column=_tz(nullable=True))
    not_after: datetime | None = Field(default=None, sa_column=_tz(nullable=True))
    nfc_capable: bool | None = None
    issuer_generation: str | None = None

    # Google, derived from the service account JSON.
    service_account_email: str | None = None
    private_key_id: str | None = None
    project_id: str | None = None
    issuer_id: str | None = None

    certificate_pem: str | None = None
    csr_pem: str | None = None

    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))
    updated_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))


class SecretBlob(Base, table=True):
    """Encrypted secret material (private key or service account JSON)."""

    __tablename__ = "secret_blob"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    credential_set_id: UUID = Field(foreign_key="credential_set.id", index=True)
    kind: SecretKind = Field(
        sa_column=_enum_column(_enum_type(SecretKind, "secret_kind"), nullable=False)
    )
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    algo: str
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))


class Template(Base, table=True):
    """A logical credential, for example a student identity card."""

    __tablename__ = "template"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenant.id", index=True)
    key: str
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))
    archived_at: datetime | None = Field(default=None, sa_column=_tz(nullable=True))


class TemplateVariant(Base, table=True):
    """One design of a template for one wallet platform."""

    __tablename__ = "template_variant"
    __table_args__ = (
        UniqueConstraint("template_id", "wallet_type", "key"),
        Index(
            "uq_variant_default",
            "template_id",
            "wallet_type",
            unique=True,
            postgresql_where="is_default",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    template_id: UUID = Field(foreign_key="template.id", index=True)
    wallet_type: WalletType = Field(
        sa_column=_enum_column(_wallet_type, nullable=False)
    )
    key: str
    name: str
    is_default: bool = False
    credential_set_id: UUID | None = Field(
        default=None, foreign_key="credential_set.id"
    )
    google_class_id: str | None = None
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))
    archived_at: datetime | None = Field(default=None, sa_column=_tz(nullable=True))


class TemplateVersion(Base, table=True):
    """Everything that determines rendering: content, assets, mapping rules.

    A published version is immutable. The platform payload check below
    enforces that a version carries at least one complete platform payload
    (Apple's ``pass_json``, or Google's ``class_json`` and ``object_json``
    together) -- the row cannot itself see its variant's ``wallet_type``, so
    this is the strongest per-row equivalent of the spec's "apple ⇒
    pass_json not null" / "google ⇒ class_json + object_json not null"
    rules that a CHECK constraint (no trigger) can express.
    """

    __tablename__ = "template_version"
    __table_args__ = (
        UniqueConstraint("variant_id", "number"),
        Index(
            "uq_version_published",
            "variant_id",
            unique=True,
            postgresql_where="status = 'published'",
        ),
        CheckConstraint(
            "pass_json IS NOT NULL "
            "OR (class_json IS NOT NULL AND object_json IS NOT NULL)",
            name="ck_template_version_platform_payload",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    variant_id: UUID = Field(foreign_key="template_variant.id", index=True)
    number: int
    status: VersionStatus = Field(
        default=VersionStatus.DRAFT,
        sa_column=_enum_column(
            _enum_type(VersionStatus, "version_status"), nullable=False
        ),
    )
    pass_json: dict | None = Field(default=None, sa_column=Column(JSONB))
    class_json: dict | None = Field(default=None, sa_column=Column(JSONB))
    object_json: dict | None = Field(default=None, sa_column=Column(JSONB))
    source_object_key: str | None = None
    nfc_enabled: bool = False
    nfc_encryption_public_key: str | None = None
    nfc_requires_authentication: bool = False
    notes: str | None = None
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))
    created_by: UUID | None = Field(default=None, foreign_key="api_client.id")
    published_at: datetime | None = Field(default=None, sa_column=_tz(nullable=True))


class TemplateAsset(Base, table=True):
    """A static file (icon, logo, background, ...) of an immutable version."""

    __tablename__ = "template_asset"
    __table_args__ = (UniqueConstraint("version_id", "filename"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    version_id: UUID = Field(foreign_key="template_version.id", index=True)
    filename: str
    media_type: str
    size: int
    sha256: str
    object_key: str
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))


class MappingRule(Base, table=True):
    """A binding from one data_provider field to one place in the pass."""

    __tablename__ = "mapping_rule"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    version_id: UUID = Field(foreign_key="template_version.id", index=True)
    origin: RuleOrigin = Field(
        sa_column=_enum_column(_enum_type(RuleOrigin, "rule_origin"), nullable=False)
    )
    target_kind: TargetKind = Field(
        sa_column=_enum_column(_enum_type(TargetKind, "target_kind"), nullable=False)
    )
    target: str
    source_field: str
    value_type: ValueType = Field(sa_column=_enum_column(_value_type, nullable=False))
    required: bool = False
    default_value: str | None = None
    position: int = 0
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))


class DataField(Base, table=True):
    """A cached entry from the data_provider field catalogue."""

    __tablename__ = "data_field"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(unique=True, index=True)
    value_type: ValueType = Field(sa_column=_enum_column(_value_type, nullable=False))
    label: str
    required: bool = False
    description: str | None = None
    created_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))
    fetched_at: datetime = Field(default_factory=_now, sa_column=_tz(nullable=False))


class AuditLog(Base, table=True):
    """An immutable record of one rendering or management operation.

    ``ts`` is this row's creation timestamp (the spec names it ``ts``
    rather than ``created_at`` since it is the audited event time itself).
    """

    __tablename__ = "audit_log"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenant.id", index=True)
    ts: datetime = Field(
        default_factory=_now, sa_column=_tz(nullable=False, index=True)
    )
    request_id: str = Field(index=True)
    actor_client_id: UUID | None = Field(default=None, foreign_key="api_client.id")
    """The machine credential that acted, where one did."""

    actor_principal: str | None = Field(default=None, index=True)
    """The person that acted, where one did -- the management UI's caller.

    A second actor column rather than a wider `actor_client_id`, because
    that one is a foreign key into `api_client` and a person has no row
    there. Without this the actions with the highest consequence --
    uploading a signing credential, publishing a version -- would be
    recorded with no actor at all, which reads exactly like an entry whose
    actor was never captured.

    Exactly one of the two is set. Deliberately not a CHECK constraint yet:
    `actor_client_id` has been nullable since the first migration and this
    is not the change that should decide what a pre-existing NULL means.
    """
    action: str
    outcome: str
    error_code: str | None = None
    duration_ms: int
    template_id: UUID | None = Field(default=None, foreign_key="template.id")
    variant_id: UUID | None = Field(default=None, foreign_key="template_variant.id")
    version_id: UUID | None = Field(default=None, foreign_key="template_version.id")
    wallet_type: WalletType | None = Field(
        default=None, sa_column=_enum_column(_wallet_type, nullable=True)
    )
    subject_ref: str | None = None
    requested_fields: list[str] = Field(sa_column=Column(ARRAY(String)))
    details: dict | None = Field(default=None, sa_column=Column(JSONB))
