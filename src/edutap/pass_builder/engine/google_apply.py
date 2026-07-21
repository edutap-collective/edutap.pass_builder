"""Apply bound values to a Google wallet object template."""

from .placeholders import resolve_placeholders
from .spec import BoundValue


def apply_google(object_json: dict, bound: list[BoundValue]) -> dict:
    """Return the object with ${…} placeholders resolved from bound values."""
    values = {item.rule.source_field: str(item.value) for item in bound}
    return resolve_placeholders(object_json, values)
