"""Tests for Apple .pkpass assembly and signing."""

import io
import json
import zipfile

from edutap.pass_builder.engine.apple_build import build_apple
from edutap.pass_builder.engine.spec import BoundValue, RenderSpec, RuleSpec
from edutap.pass_builder.models.enums import TargetKind, ValueType, WalletType


def test_serial_number_is_set_and_bytes_returned():
    """build_apple sets the serial number and returns .pkpass bytes."""
    spec = RenderSpec(
        wallet_type=WalletType.APPLE_VAS,
        pass_json={
            "formatVersion": 1,
            "description": "Test pass",
            "organizationName": "Test Org",
            "passTypeIdentifier": "pass.test.example",
            "teamIdentifier": "TEAMID123",
            "generic": {
                "primaryFields": [{"key": "name", "label": "Name", "value": ""}]
            },
        },
        assets={"icon.png": b"\x89PNG"},
    )
    bound = [
        BoundValue(
            rule=RuleSpec(
                target_kind=TargetKind.FIELD_VALUE,
                target="name",
                source_field="person.name",
                value_type=ValueType.TEXT,
            ),
            value="Ada",
        )
    ]
    captured = {}

    def fake_sign(pkpass):
        captured["serial"] = pkpass.pass_object.serialNumber

    result = build_apple(spec, bound, "serial-123", fake_sign)

    assert isinstance(result, bytes)
    assert captured["serial"] == "serial-123"


def test_build_apple_does_not_mutate_input_spec():
    """build_apple does not mutate the caller's input RenderSpec."""
    spec = RenderSpec(
        wallet_type=WalletType.APPLE_VAS,
        pass_json={
            "formatVersion": 1,
            "description": "Test pass",
            "organizationName": "Test Org",
            "passTypeIdentifier": "pass.test.example",
            "teamIdentifier": "TEAMID123",
            "generic": {
                "primaryFields": [{"key": "name", "label": "Name", "value": ""}]
            },
        },
        assets={},
    )
    bound = [
        BoundValue(
            rule=RuleSpec(
                target_kind=TargetKind.FIELD_VALUE,
                target="name",
                source_field="person.name",
                value_type=ValueType.TEXT,
            ),
            value="Ada",
        )
    ]

    def fake_sign(pkpass):
        pass

    build_apple(spec, bound, "serial-456", fake_sign)

    assert spec.pass_json is not None
    assert spec.pass_json["generic"]["primaryFields"][0]["value"] == ""


def _delivered_pass_json(pkpass_bytes: bytes) -> dict:
    """Read `pass.json` out of the built .pkpass.

    THE DELIVERED FILE, not the in-memory model. `edutap.wallet_apple` keeps
    `authenticationToken` as `bytes`, while the JSON the device actually reads
    carries a plain string -- and the string is what the verifier compares
    against. A test against the model would pass while asserting the wrong
    thing.
    """
    with zipfile.ZipFile(io.BytesIO(pkpass_bytes)) as archive:
        return json.loads(archive.read("pass.json"))


def _spec_and_sign():
    """A minimal Apple spec plus a signer that captures the assembled pass."""
    spec = RenderSpec(
        wallet_type=WalletType.APPLE_VAS,
        pass_json={
            "formatVersion": 1,
            "description": "Mensa",
            "organizationName": "Ludwig-Maximilians-Universitaet Muenchen",
            "passTypeIdentifier": "pass.de.stwm.mensapass",
            "teamIdentifier": "X623KAF9K6",
            "storeCard": {"headerFields": [], "primaryFields": []},
        },
        assets={"icon.png": b"\x89PNG"},
    )
    captured: dict = {}

    def fake_sign(pkpass):
        captured["pass_object"] = pkpass.pass_object

    return spec, captured, fake_sign


def test_the_authentication_token_reaches_the_built_pass():
    """The wiring, not the arithmetic -- `test_apple_token.py` pins the arithmetic.

    A token that is computed correctly and never written is the same failure as
    no token at all: every device gets 401, the passes stop updating without an
    error anywhere, and seven days later they expire on their own.

    The expected value is the one the VERIFIER produced
    (`edutap.wallet_apple_vas_web_service.tokens.derive_token`, 2026-09-13) for
    exactly this pass type identifier and serial number.
    """
    spec, _captured, fake_sign = _spec_and_sign()

    pkpass = build_apple(
        spec,
        [],
        "11f71528-4d50-4279-9df3-e7388154eed5",
        fake_sign,
        authentication_secret="probe-geheimnis",  # noqa: S106 - Probewert
    )

    assert _delivered_pass_json(pkpass)["authenticationToken"] == (
        "e74b0dc68b888d2d8e87ea39a3e0f0994bb81ea43d4ba49bb3eda56a608892aa"
    )


def test_without_a_secret_no_token_is_invented():
    """Empty means "leave it alone" -- a deployment without Apple passes needs none."""
    spec, _captured, fake_sign = _spec_and_sign()

    pkpass = build_apple(spec, [], "serial-123", fake_sign, authentication_secret="")

    assert not _delivered_pass_json(pkpass).get("authenticationToken")


def test_the_token_follows_the_serial_number():
    """Two passes of one template must not share a token.

    That is the entire reason the value is derived instead of stored: a
    `.pkpass` is a ZIP whose `pass.json` its holder can read, and the delivery
    endpoint hands out the full pass to whoever presents a matching token.
    """
    tokens = []
    for serial in ("serial-a", "serial-b"):
        spec, _captured, fake_sign = _spec_and_sign()
        pkpass = build_apple(
            spec,
            [],
            serial,
            fake_sign,
            authentication_secret="probe-geheimnis",  # noqa: S106 - Probewert
        )
        tokens.append(_delivered_pass_json(pkpass)["authenticationToken"])

    assert tokens[0] != tokens[1]
