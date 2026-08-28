"""Tests for Apple .pkpass assembly and signing."""

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
