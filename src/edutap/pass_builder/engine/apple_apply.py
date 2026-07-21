"""Apply bound values to an Apple pass.json structure and its assets."""

from typing import Any

from ..models.enums import TargetKind
from .spec import BoundValue

_FIELD_GROUPS = (
    "headerFields",
    "primaryFields",
    "secondaryFields",
    "auxiliaryFields",
    "backFields",
)
_STYLES = ("boardingPass", "coupon", "eventTicket", "generic", "storeCard")
_NFC_MAX = 64


class NfcPayloadTooLongError(Exception):
    """Raised when an Apple NFC message exceeds 64 characters."""

    def __init__(self, length: int) -> None:
        """Store the length of the payload that exceeded the limit."""
        super().__init__(f"nfc payload has {length} characters, limit is {_NFC_MAX}")
        self.length = length


def _set_field(pass_json: dict, key: str, attribute: str, value: str) -> None:
    for style in _STYLES:
        style_block = pass_json.get(style)
        if not isinstance(style_block, dict):
            continue
        for group in _FIELD_GROUPS:
            for field in style_block.get(group, []):
                if field.get("key") == key:
                    field[attribute] = value


def _set_pointer(obj: Any, pointer: str, value: str) -> None:
    parts = [
        p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")
    ]
    cursor = obj
    for part in parts[:-1]:
        if isinstance(cursor, list):
            cursor = cursor[int(part)]
        else:
            cursor = cursor[part]
    last = parts[-1]
    if isinstance(cursor, list):
        cursor[int(last)] = value
    else:
        cursor[last] = value


def apply_apple(
    pass_json: dict,
    assets: dict[str, bytes],
    bound: list[BoundValue],
) -> tuple[dict, dict[str, bytes]]:
    """Return the pass dict and asset map with all bound values applied."""
    for item in bound:
        kind = item.rule.target_kind
        target = item.rule.target
        value_str = str(item.value)
        if kind == TargetKind.FIELD_VALUE:
            _set_field(pass_json, target, "value", value_str)
        elif kind == TargetKind.FIELD_LABEL:
            _set_field(pass_json, target, "label", value_str)
        elif kind == TargetKind.IMAGE and isinstance(item.value, bytes):
            assets[target] = item.value
        elif kind == TargetKind.BARCODE_MESSAGE:
            pass_json.setdefault("barcodes", [{}])[0]["message"] = value_str
        elif kind == TargetKind.BARCODE_ALT_TEXT:
            pass_json.setdefault("barcodes", [{}])[0]["altText"] = value_str
        elif kind == TargetKind.NFC_PAYLOAD:
            if len(value_str) > _NFC_MAX:
                raise NfcPayloadTooLongError(len(value_str))
            pass_json.setdefault("nfc", {})["message"] = value_str
        elif kind == TargetKind.JSON_POINTER:
            _set_pointer(pass_json, target, value_str)
    return pass_json, assets
