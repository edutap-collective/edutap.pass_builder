"""The token both services compute, pinned by a vector neither of them owns alone.

`edutap.pass_builder` writes the token into the pass;
`edutap.wallet_apple_vas_web_service` recomputes it to verify an
`Authorization: ApplePass <token>` header. Neither imports the other, so nothing
in the type system stops the two from drifting apart.

THE VECTOR BELOW WAS PRODUCED BY THE VERIFIER, not by the code under test. It
came out of `edutap.wallet_apple_vas_web_service.tokens.derive_token` on
2026-09-13, and `verify_authorization` accepted it. If this test ever fails, the
builder and the verifier no longer agree -- and that failure mode is otherwise
invisible: every device would get `401` on every update, the passes would stop
updating without an error anywhere, and seven days later they would expire on
their own.
"""

from edutap.pass_builder.engine.apple_token import derive_token

#: Fixed inputs. The pass type identifier is the real one for the canteen pass;
#: the secret is a probe value and not a secret of any deployment.
_SECRET = "probe-geheimnis"  # noqa: S105 - Probewert, kein Geheimnis eines Deployments
_PASS_TYPE_IDENTIFIER = "pass.de.stwm.mensapass"  # noqa: S105 - kein Geheimnis
_SERIAL_NUMBER = "11f71528-4d50-4279-9df3-e7388154eed5"

#: Produced by the verifier's own implementation. Do not recompute this with the
#: code under test -- that would make the test agree with whatever it does.
_EXPECTED = "e74b0dc68b888d2d8e87ea39a3e0f0994bb81ea43d4ba49bb3eda56a608892aa"


def test_matches_the_vector_the_verifier_produced() -> None:
    assert derive_token(_SECRET, _PASS_TYPE_IDENTIFIER, _SERIAL_NUMBER) == _EXPECTED


def test_the_token_is_lowercase_hex() -> None:
    """Case and encoding are part of the contract -- the verifier compares strings."""
    token = derive_token(_SECRET, _PASS_TYPE_IDENTIFIER, _SERIAL_NUMBER)
    assert len(token) == 64
    assert token == token.lower()
    bytes.fromhex(token)


def test_the_separator_keeps_the_pairs_apart() -> None:
    r"""Without `\x00`, ("pass.a", "bc") and ("pass.ab", "c") would sign the same
    bytes.

    Two passes would then share a token, and one holder could read the other's
    pass -- precisely what deriving the token instead of storing it prevents.
    """
    assert derive_token(_SECRET, "pass.a", "bc") != derive_token(
        _SECRET, "pass.ab", "c"
    )


def test_a_different_secret_yields_a_different_token() -> None:
    """The property rotation relies on: the verifier tries several secrets."""
    assert derive_token("andere", _PASS_TYPE_IDENTIFIER, _SERIAL_NUMBER) != _EXPECTED


def test_a_different_serial_yields_a_different_token() -> None:
    """Possessing one pass's token must not yield another's -- the whole point."""
    assert derive_token(_SECRET, _PASS_TYPE_IDENTIFIER, "andere-serie") != _EXPECTED
