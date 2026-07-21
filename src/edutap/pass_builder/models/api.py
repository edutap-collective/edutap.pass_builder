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
from .enums import CredentialStatus, Provider, ValueType, WalletType


class CreatePassRequest(BaseModel):
    """Request body to create or update a rendered pass."""

    pass_id: str
    template: str
    wallet_type: WalletType
    variant: str | None = None
    person_uid: str
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


class MappingRulesRequest(BaseModel):
    """Request body to replace a draft version's mapping rules."""

    rules: list[RuleSpec]


class CreateCredentialRequest(BaseModel):
    """Request body to create, import or install a credential set."""

    provider: Provider
    label: str
    common_name: str | None = None
    """Apple only: the CSR subject common name for `create_apple`."""
    issuer_id: str | None = None
    """Google only: the Google Wallet issuer id."""


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
