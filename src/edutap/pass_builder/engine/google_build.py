"""Assemble a Google wallet object from a render spec."""

from .google_apply import apply_google
from .spec import BoundValue, RenderSpec


def google_object_id(issuer_id: str, pass_uuid: str) -> str:
    """Return the stable object id, independent of template and variant."""
    return f"{issuer_id}.{pass_uuid}"


def build_google_object(
    spec: RenderSpec,
    bound: list[BoundValue],
    object_id: str,
    class_id: str,
) -> dict:
    """Return the wallet object with placeholders resolved and ids set."""
    obj = apply_google(dict(spec.object_json or {}), bound)
    obj["id"] = object_id
    obj["classId"] = class_id
    return obj
