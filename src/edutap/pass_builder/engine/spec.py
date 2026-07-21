"""Pure input models for the rendering engine."""

from pydantic import BaseModel

from ..models.enums import TargetKind, ValueType, WalletType


class RuleSpec(BaseModel):
    """A single substitution rule, decoupled from the database row."""

    target_kind: TargetKind
    target: str
    source_field: str
    value_type: ValueType
    required: bool = True
    default_value: str | None = None
    position: int = 0


class RenderSpec(BaseModel):
    """Everything the engine needs to render one pass, free of I/O."""

    wallet_type: WalletType
    pass_json: dict | None = None
    class_json: dict | None = None
    object_json: dict | None = None
    assets: dict[str, bytes] = {}
    rules: list[RuleSpec] = []
    nfc_enabled: bool = False
    nfc_encryption_public_key: str | None = None
    nfc_requires_authentication: bool = False
    issuer_id: str | None = None


class BoundValue(BaseModel):
    """A rule paired with its resolved, converted value."""

    model_config = {"arbitrary_types_allowed": True}

    rule: RuleSpec
    value: str | bytes
