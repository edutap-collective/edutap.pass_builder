"""Assemble and sign an Apple .pkpass from a render spec."""

import copy
from collections.abc import Callable

from edutap.wallet_apple import api

from .apple_apply import apply_apple
from .spec import BoundValue, RenderSpec


def build_apple(
    spec: RenderSpec,
    bound: list[BoundValue],
    serial_number: str,
    sign: Callable[[object], None],
) -> bytes:
    """Return signed .pkpass bytes for the given spec and bound values."""
    pass_json = copy.deepcopy(spec.pass_json or {})
    pass_json["serialNumber"] = serial_number
    if spec.nfc_enabled:
        nfc = pass_json.setdefault("nfc", {})
        if spec.nfc_encryption_public_key:
            nfc["encryptionPublicKey"] = spec.nfc_encryption_public_key
        nfc["requiresAuthentication"] = spec.nfc_requires_authentication
    pass_json, assets = apply_apple(pass_json, dict(spec.assets), bound)
    pkpass = api.new(data=pass_json)
    for filename, data in assets.items():
        pkpass.files[filename] = data
    sign(pkpass)
    return api.pkpass(pkpass).read()
