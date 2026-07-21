from datetime import date

import pytest

from edutap.pass_builder.engine.binding import MissingFieldsError, bind, required_fields
from edutap.pass_builder.engine.spec import RuleSpec
from edutap.pass_builder.models.enums import TargetKind, ValueType


def rule(source_field, value_type=ValueType.TEXT, required=True, default_value=None):
    return RuleSpec(
        target_kind=TargetKind.FIELD_VALUE,
        target="name",
        source_field=source_field,
        value_type=value_type,
        required=required,
        default_value=default_value,
        position=0,
    )


def test_date_is_converted_to_iso_8601():
    bound = bind(
        [rule("person.valid_until", ValueType.DATE)],
        {"person.valid_until": date(2027, 3, 31)},
    )
    assert bound[0].value == "2027-03-31"


def test_number_uses_canonical_decimal():
    bound = bind([rule("person.credits", ValueType.NUMBER)], {"person.credits": 42})
    assert bound[0].value == "42"


def test_missing_required_field_lists_all_missing_at_once():
    rules = [rule("person.name"), rule("person.email")]
    with pytest.raises(MissingFieldsError) as excinfo:
        bind(rules, {})
    assert excinfo.value.fields == ["person.name", "person.email"]


def test_default_value_is_used_when_field_absent():
    bound = bind([rule("person.title", required=False, default_value="—")], {})
    assert bound[0].value == "—"


def test_required_fields_are_deduplicated_and_ordered():
    rules = [rule("person.name"), rule("person.name"), rule("person.email")]
    assert required_fields(rules) == ["person.name", "person.email"]
