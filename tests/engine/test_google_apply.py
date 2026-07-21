from edutap.pass_builder.engine.google_apply import apply_google
from edutap.pass_builder.engine.spec import BoundValue, RuleSpec
from edutap.pass_builder.models.enums import TargetKind, ValueType


def bound(source_field, value):
    return BoundValue(
        rule=RuleSpec(
            target_kind=TargetKind.JSON_POINTER,
            target="/x",
            source_field=source_field,
            value_type=ValueType.TEXT,
        ),
        value=value,
    )


def test_placeholders_are_resolved_from_bound_values():
    object_json = {"cardTitle": {"defaultValue": {"value": "${person.name}"}}}
    result = apply_google(object_json, [bound("person.name", "Ada")])
    assert result["cardTitle"]["defaultValue"]["value"] == "Ada"
