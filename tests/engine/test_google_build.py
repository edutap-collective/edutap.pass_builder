from edutap.pass_builder.engine.google_build import (
    build_google_object,
    google_object_id,
)
from edutap.pass_builder.engine.spec import BoundValue, RenderSpec, RuleSpec
from edutap.pass_builder.models.enums import TargetKind, ValueType, WalletType


def test_object_id_is_issuer_dot_uuid():
    assert google_object_id("3388", "abc-uuid") == "3388.abc-uuid"


def test_object_carries_id_class_and_resolved_values():
    spec = RenderSpec(
        wallet_type=WalletType.GOOGLE_ST,
        object_json={"cardTitle": {"defaultValue": {"value": "${person.name}"}}},
    )
    bound = [
        BoundValue(
            rule=RuleSpec(
                target_kind=TargetKind.JSON_POINTER,
                target="/x",
                source_field="person.name",
                value_type=ValueType.TEXT,
            ),
            value="Ada",
        )
    ]
    obj = build_google_object(spec, bound, "3388.abc", "3388.student")
    assert obj["id"] == "3388.abc"
    assert obj["classId"] == "3388.student"
    assert obj["cardTitle"]["defaultValue"]["value"] == "Ada"
