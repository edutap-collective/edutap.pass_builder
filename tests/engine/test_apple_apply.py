import pytest

from edutap.pass_builder.engine.apple_apply import NfcPayloadTooLongError, apply_apple
from edutap.pass_builder.engine.spec import BoundValue, RuleSpec
from edutap.pass_builder.models.enums import TargetKind, ValueType


def bound(target_kind, target, value):  # noqa: ANN001, ANN002
    return BoundValue(
        rule=RuleSpec(
            target_kind=target_kind,
            target=target,
            source_field="x",
            value_type=ValueType.TEXT,
        ),
        value=value,
    )


def base_pass():
    return {
        "generic": {"primaryFields": [{"key": "name", "label": "Name", "value": ""}]},
        "barcodes": [{"message": "", "format": "PKBarcodeFormatQR"}],
    }


def test_field_value_is_written_by_key():
    bound_val = bound(TargetKind.FIELD_VALUE, "name", "Ada")
    result, _ = apply_apple(base_pass(), {}, [bound_val])
    assert result["generic"]["primaryFields"][0]["value"] == "Ada"


def test_image_replaces_asset():
    bound_val = bound(TargetKind.IMAGE, "icon.png", b"new")
    _, assets = apply_apple(base_pass(), {"icon.png": b"old"}, [bound_val])
    assert assets["icon.png"] == b"new"


def test_barcode_message_is_written():
    bound_val = bound(TargetKind.BARCODE_MESSAGE, "", "PAYLOAD")
    result, _ = apply_apple(base_pass(), {}, [bound_val])
    assert result["barcodes"][0]["message"] == "PAYLOAD"


def test_nfc_payload_over_64_chars_is_rejected():
    bound_val = bound(TargetKind.NFC_PAYLOAD, "", "x" * 65)
    with pytest.raises(NfcPayloadTooLongError):
        apply_apple(base_pass(), {}, [bound_val])


def test_nfc_payload_within_limit_is_written():
    bound_val = bound(TargetKind.NFC_PAYLOAD, "", "token")
    result, _ = apply_apple(base_pass(), {}, [bound_val])
    assert result["nfc"]["message"] == "token"
