"""The per-pass authentication token Apple sends back on every update request.

WHY IT IS DERIVED AND NOT STORED. A `.pkpass` is a ZIP whose `pass.json` every
holder can read. An issuer-wide token would therefore be in the hands of every
holder, and `GET /v1/passes/{type}/{serial}` returns the full pass to whoever
presents one -- one holder could read every other holder's pass. The token is
derived from the pass's own identity instead, so possessing one yields no other.

BOTH SIDES COMPUTE, NEITHER SIDE TRANSFERS. This service writes the token while
building the pass; `edutap.wallet_apple_vas_web_service` computes it again while
verifying an `Authorization: ApplePass <token>` header. That needs no write path
between the two, and it authenticates a registration for a pass the web service
has never seen -- the ordinary case right after a pass is installed.

THE ALGORITHM IS DUPLICATED HERE ON PURPOSE, and that is a risk worth naming.
The source of truth is `edutap.wallet_apple_vas_web_service.tokens.derive_token`.
This package must not depend on that one -- the builder has no business importing
the verifier -- so the two are pinned together by a SHARED TEST VECTOR instead:
`tests/test_apple_token.py` carries a value produced by the verifier's own
implementation. Change either side and that test fails.

A mismatch would not be visible as an error. Every device would receive `401` on
every update, the passes would silently stop updating, and seven days later they
would expire on their own -- which is exactly the safety net that is supposed to
catch a broken chain, not to be the symptom of one.
"""

import hmac
from hashlib import sha256

#: Separates the two identifiers in the signed message.
#:
#: A byte that occurs in neither a pass type identifier nor a serial number, so
#: no two different pairs can produce the same message by concatenation. Without
#: it, ("pass.a", "bc") and ("pass.ab", "c") would sign the same bytes.
_SEPARATOR = b"\x00"


def derive_token(secret: str, pass_type_identifier: str, serial_number: str) -> str:
    r"""Return the authentication token of one pass, as lowercase hex.

    Mirrors the verifier byte for byte: UTF-8 for both identifiers and the
    secret, `\\x00` between them, HMAC-SHA256, `hexdigest()` -- lowercase.
    """
    message = (
        pass_type_identifier.encode("utf-8")
        + _SEPARATOR
        + serial_number.encode("utf-8")
    )
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


__all__ = ["derive_token"]
