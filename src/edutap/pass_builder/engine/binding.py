"""Bind data-provider values to mapping rules and convert their types."""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..models.enums import ValueType
from .spec import BoundValue, RuleSpec


class MissingFieldsError(Exception):
    """Raised when required source fields are absent from the data."""

    def __init__(self, fields: list[str]) -> None:
        """Store the list of missing required source-field names."""
        super().__init__(f"missing required fields: {', '.join(fields)}")
        self.fields = fields


def required_fields(rules: list[RuleSpec]) -> list[str]:
    """Return the deduplicated, order-preserving list of required source fields."""
    seen: dict[str, None] = {}
    for rule in rules:
        if rule.required:
            seen.setdefault(rule.source_field, None)
    return list(seen)


def _convert(value: Any, value_type: ValueType) -> str | bytes:
    if value_type == ValueType.DATE:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)
    if value_type == ValueType.NUMBER:
        return str(Decimal(str(value)).normalize())
    if value_type == ValueType.BOOLEAN:
        return "true" if value else "false"
    if value_type == ValueType.IMAGE:
        # AN IMAGE BINDS A REFERENCE, NOT A PICTURE. The data provider answers
        # JSON and JSON has no bytes, so a value arriving from there is always
        # a URL and falls through to `str` below. `services/render.py` fetches
        # it where the platform needs the bytes -- Apple, whose `.pkpass`
        # carries the file; Google takes the URL as it stands.
        #
        # Bytes still pass through untouched, and only one caller produces
        # them: `RenderService.preview`, whose generated placeholder for an
        # image is a short PNG header. A preview is design-time and reaches no
        # network, so its picture cannot come from a reference.
        #
        # Until 2026-09-01 this branch was the *only* way an image could carry
        # anything, which meant it never did: the condition below can never
        # hold for a value out of JSON, `str(value)` took over, and
        # `apple_apply` writes the asset only for bytes. The rule bound, the
        # version published green, and the picture was missing.
        if isinstance(value, bytes):
            return value
    return str(value)


def bind(rules: list[RuleSpec], data: Mapping[str, Any]) -> list[BoundValue]:
    """Resolve every rule against the data, collecting all missing fields."""
    bound: list[BoundValue] = []
    missing: list[str] = []
    for rule in rules:
        if rule.source_field in data:
            raw = data[rule.source_field]
        elif rule.default_value is not None:
            raw = rule.default_value
        elif rule.required:
            if rule.source_field not in missing:
                missing.append(rule.source_field)
            continue
        else:
            continue
        bound.append(BoundValue(rule=rule, value=_convert(raw, rule.value_type)))
    if missing:
        raise MissingFieldsError(missing)
    return bound
