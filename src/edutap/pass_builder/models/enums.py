"""Enumerations shared by the database models and the API schemas.

`WalletType` is NOT defined here any more. It comes from `edutap.data_models`, which
is where the estate keeps its shared vocabulary -- the same reasoning that put the
settings prefixes there. What stood here was a copy on the coarse provider axis
(`apple`, `google`, `samsung`), and a copy of a shared vocabulary is a second truth:
it could not say whether an Apple pass is VAS, Access or Identity, so a caller that
needed Access had to go somewhere else entirely and this service could not even
explain why.
"""

from enum import StrEnum

from edutap.data_models.vocabulary import WalletType

#: What this service can actually build today.
#:
#: VAS and Smart Tap -- the two that carry a pass in a wallet. Access and Identity are
#: different credential technologies, not variants of these: they involve secure
#: element provisioning that this service does not do at all. Asking for one is
#: therefore answered `501`, not `400`: the request is well formed and the wallet type
#: is real, this service simply does not implement it. A `400` would tell the caller
#: to fix something that is not wrong.
#:
#: EUDI is absent for the same reason and one more: nothing here speaks verifiable
#: credentials yet.
SUPPORTED_WALLET_TYPES = frozenset({WalletType.APPLE_VAS, WalletType.GOOGLE_ST})

#: The Apple wallet types, for the branches that ask "signed bytes or JSON?".
#:
#: A set rather than a comparison against one member: `APPLE_VAS` is the only one
#: supported today, but the question these branches ask is about the PLATFORM, and a
#: branch written as `== WalletType.APPLE_VAS` would silently take the Google path the
#: day Access arrives.
APPLE_WALLET_TYPES = frozenset(
    {WalletType.APPLE_VAS, WalletType.APPLE_ACCESS, WalletType.APPLE_IDENTITY}
)

#: The Google wallet types, same reasoning.
GOOGLE_WALLET_TYPES = frozenset(
    {WalletType.GOOGLE_ST, WalletType.GOOGLE_ACCESS, WalletType.GOOGLE_IDENTITY}
)


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
