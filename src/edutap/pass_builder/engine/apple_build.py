"""Assemble and sign an Apple .pkpass from a render spec."""

import copy
from collections.abc import Callable

from edutap.wallet_apple import api

from .apple_apply import apply_apple
from .apple_token import derive_token
from .spec import BoundValue, RenderSpec


def build_apple(
    spec: RenderSpec,
    bound: list[BoundValue],
    serial_number: str,
    sign: Callable[[object], None],
    authentication_secret: str = "",
) -> bytes:
    """Return signed .pkpass bytes for the given spec and bound values.

    `authentication_secret` derives this pass's `authenticationToken`. Empty
    means "leave it alone": whatever the template carries stays, and a template
    that carries nothing produces a pass without one. See `apple_token`.
    """
    pass_json = copy.deepcopy(spec.pass_json or {})
    pass_json["serialNumber"] = serial_number
    # AFTER the serial number, because the token is derived from it -- together
    # with the pass type identifier, which is the template's and not ours to
    # invent. A template without one is a template that cannot be verified, so
    # the token is written only when both halves are there.
    pass_type_identifier = pass_json.get("passTypeIdentifier")
    if authentication_secret and pass_type_identifier:
        pass_json["authenticationToken"] = derive_token(
            authentication_secret, pass_type_identifier, serial_number
        )
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
