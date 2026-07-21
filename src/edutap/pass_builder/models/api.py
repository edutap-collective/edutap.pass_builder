"""API request and response schemas.

These are deliberately kept separate from the SQLModel table definitions in
`models/db.py`: an API schema exposes only what a client should see, while a
DB model exposes every persisted column, including secret material. Nothing
in `models/db.py` is re-exported here.

`CredentialResponse` in particular must never carry `private_key`,
`service_account_json`, `ciphertext`, `nonce` or `wrapped_dek` -- those live
only in `SecretBlob` and are opened exclusively by
`services.credentials.CredentialService.open_material` for the render path.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from ..engine.spec import RuleSpec
from .enums import CredentialStatus, Provider, ValueType, VersionStatus, WalletType


class CreatePassRequest(BaseModel):
    """Request body to create or update a rendered pass."""

    pass_id: str
    template: str
    wallet_type: WalletType
    variant: str | None = None
    person_uid: str
    template_version: int | None = None


class UpdatePassRequest(BaseModel):
    """Request body to re-render an existing pass. Same as create, minus the id."""

    template: str
    wallet_type: WalletType
    variant: str | None = None
    person_uid: str
    template_version: int | None = None


class SaveLinkRequest(BaseModel):
    """Request body to generate a Google "save to wallet" link."""

    template: str
    variant: str | None = None
    template_version: int | None = None


class GooglePassResponse(BaseModel):
    """Response describing a rendered Google Wallet pass."""

    pass_id: str
    object_id: str
    class_id: str
    template_version: int
    variant: str


class CreateTemplateRequest(BaseModel):
    """Request body to create a new template."""

    key: str
    name: str
    description: str | None = None


class TemplateResponse(BaseModel):
    """Response describing a template."""

    id: UUID
    key: str
    name: str
    description: str | None = None
    created_at: datetime
    archived_at: datetime | None = None


class UpdateTemplateRequest(BaseModel):
    """Request body to patch a template's name or description."""

    name: str | None = None
    description: str | None = None


class CreateVariantRequest(BaseModel):
    """Request body to create a new template variant."""

    key: str
    name: str
    wallet_type: WalletType
    is_default: bool = False
    credential_set_id: UUID | None = None
    google_class_id: str | None = None


class VariantResponse(BaseModel):
    """Response describing a template variant."""

    id: UUID
    template_id: UUID
    key: str
    name: str
    wallet_type: WalletType
    is_default: bool
    credential_set_id: UUID | None = None
    google_class_id: str | None = None
    created_at: datetime
    archived_at: datetime | None = None


class UpdateVariantRequest(BaseModel):
    """Request body to patch a variant's default flag or credential/class id."""

    name: str | None = None
    is_default: bool | None = None
    credential_set_id: UUID | None = None
    google_class_id: str | None = None


class CreateGoogleVersionRequest(BaseModel):
    """Request body to create a draft Google version (JSON, not multipart)."""

    class_json: dict[str, Any]
    object_json: dict[str, Any]


class VersionResponse(BaseModel):
    """Response describing a template version."""

    id: UUID
    variant_id: UUID
    number: int
    status: VersionStatus
    nfc_enabled: bool
    nfc_encryption_public_key: str | None = None
    nfc_requires_authentication: bool = False
    notes: str | None = None
    created_at: datetime
    published_at: datetime | None = None


class MappingRulesRequest(BaseModel):
    """Request body to replace a draft version's mapping rules."""

    rules: list[RuleSpec]


class MappingRulesResponse(BaseModel):
    """Response listing a version's mapping rules."""

    rules: list[RuleSpec]


class AssetResponse(BaseModel):
    """Response describing one stored template asset (metadata only)."""

    filename: str
    media_type: str
    size: int
    sha256: str
    created_at: datetime


class ValidationResponse(BaseModel):
    """Response describing the outcome of a (non-publishing) validation run."""

    valid: bool
    findings: list[str] = []


class CreateCredentialRequest(BaseModel):
    """Request body to create, import or install a credential set."""

    provider: Provider
    label: str
    common_name: str | None = None
    """Apple only: the CSR subject common name for `create_apple`."""
    issuer_id: str | None = None
    """Google only: the Google Wallet issuer id."""
    service_account_json: dict[str, Any] | None = None
    """Google only: the service account key file to import."""


class InstallCertificateRequest(BaseModel):
    """Request body to install a signed certificate onto a pending credential."""

    certificate_pem: str


class CredentialResponse(BaseModel):
    """Metadata about a credential set.

    Exposes only metadata -- never `private_key`, `service_account_json`,
    `ciphertext`, `nonce` or `wrapped_dek`. See the module docstring.
    """

    id: UUID
    provider: Provider
    label: str
    status: CredentialStatus
    pass_type_identifier: str | None = None
    team_identifier: str | None = None
    organization_name: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    nfc_capable: bool | None = None
    service_account_email: str | None = None
    issuer_id: str | None = None
    cert_fingerprint_sha256: str | None = None


class PreviewRequest(BaseModel):
    """Request body to render a pass/object preview from sample data."""

    template: str
    wallet_type: WalletType
    variant: str | None = None
    template_version: int | None = None
    sample_data: dict[str, Any] | None = None


class PreviewResponse(BaseModel):
    """Response with the resolved pass/object preview, never signed or pushed."""

    pass_json: dict[str, Any] | None = None
    object_json: dict[str, Any] | None = None
    bound_fields: list[str]


class FieldResponse(BaseModel):
    """Response describing one entry of the data-provider field catalogue."""

    key: str
    value_type: ValueType
    label: str
    required: bool
    description: str | None = None


class AuditEntryResponse(BaseModel):
    """Response describing one audit log entry."""

    id: UUID
    ts: datetime
    request_id: str
    actor_client_id: UUID | None = None
    action: str
    outcome: str
    error_code: str | None = None
    duration_ms: int
    template_id: UUID | None = None
    variant_id: UUID | None = None
    version_id: UUID | None = None
    wallet_type: WalletType | None = None
    subject_ref: str | None = None
    requested_fields: list[str]
