"""Enumerations shared by the database models and the API schemas."""

from enum import StrEnum


class WalletType(StrEnum):
    """Wallet platform a variant targets."""

    APPLE = "apple"
    GOOGLE = "google"
    SAMSUNG = "samsung"


class Provider(StrEnum):
    """Credential provider."""

    APPLE = "apple"
    GOOGLE = "google"


class CredentialStatus(StrEnum):
    """Lifecycle state of a credential set."""

    KEY_PENDING = "key_pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"


class VersionStatus(StrEnum):
    """Lifecycle state of a template version."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class RuleOrigin(StrEnum):
    """Whether a mapping rule was authored or derived on publish."""

    AUTHORED = "authored"
    DERIVED = "derived"


class TargetKind(StrEnum):
    """What a mapping rule writes into the pass."""

    FIELD_VALUE = "field_value"
    FIELD_LABEL = "field_label"
    BARCODE_MESSAGE = "barcode_message"
    BARCODE_ALT_TEXT = "barcode_alt_text"
    IMAGE = "image"
    NFC_PAYLOAD = "nfc_payload"
    JSON_POINTER = "json_pointer"


class ValueType(StrEnum):
    """Type of the value a mapping rule binds."""

    TEXT = "text"
    DATE = "date"
    NUMBER = "number"
    BOOLEAN = "boolean"
    IMAGE = "image"
    URI = "uri"


class SecretKind(StrEnum):
    """Kind of secret material stored for a credential set."""

    PRIVATE_KEY = "private_key"
    SERVICE_ACCOUNT_JSON = "service_account_json"


class Scope(StrEnum):
    """API client scopes."""

    RENDER = "render"
    MANAGE = "manage"
    CREDENTIALS = "credentials"
