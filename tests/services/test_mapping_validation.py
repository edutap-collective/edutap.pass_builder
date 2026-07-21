from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind, ValueType
from edutap.pass_builder.services.mapping_validation import validate_mapping_rules


def rule(source_field, value_type=ValueType.TEXT):
    return RuleSpec(
        target_kind=TargetKind.FIELD_VALUE,
        target="name",
        source_field=source_field,
        value_type=value_type,
    )


def test_unknown_field_is_reported():
    problems = validate_mapping_rules([rule("person.unknown")], {"person.name": "text"})
    assert any("person.unknown" in p for p in problems)


def test_type_mismatch_is_reported():
    problems = validate_mapping_rules(
        [rule("person.name", ValueType.DATE)], {"person.name": "text"}
    )
    assert any("person.name" in p and "type" in p.lower() for p in problems)


def test_valid_rule_yields_no_problems():
    assert validate_mapping_rules([rule("person.name")], {"person.name": "text"}) == []
