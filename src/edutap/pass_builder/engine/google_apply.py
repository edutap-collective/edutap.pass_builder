"""Apply bound values to a Google wallet object template."""

# Prefer the upstreamed resolver from edutap.wallet_google if available;
# fall back to the local module (source of truth until wallet_google ships).
try:
    from edutap.wallet_google.placeholders import (  # ty: ignore[unresolved-import]
        resolve_placeholders,
    )
except ImportError:
    from .placeholders import resolve_placeholders

from .spec import BoundValue


def apply_google(object_json: dict, bound: list[BoundValue]) -> dict:
    """Return the object with ${…} placeholders resolved from bound values."""
    values = {item.rule.source_field: str(item.value) for item in bound}
    return resolve_placeholders(object_json, values)
