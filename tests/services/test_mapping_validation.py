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
    problems = validate_mapping_rules(
        [rule("person.unknown")], {"person.name": {"text"}}
    )
    assert any("person.unknown" in p for p in problems)


def test_type_mismatch_is_reported():
    problems = validate_mapping_rules(
        [rule("person.name", ValueType.DATE)], {"person.name": {"text"}}
    )
    assert any("person.name" in p and "type" in p.lower() for p in problems)


def test_valid_rule_yields_no_problems():
    catalogue = {"person.name": {"text"}}
    assert validate_mapping_rules([rule("person.name")], catalogue) == []


def test_a_field_good_for_several_things_accepts_each_of_them():
    """One field, several kinds, several honest bindings.

    `pass_valid_until` arrives from the data provider as STRING, TEXT and
    DATETIME. An author may put it on the pass as a date or render it as text,
    and both are correct -- so both must validate. The check that stood here
    compared against a single type and would have rejected one of the two,
    depending on which type the translation had picked.
    """
    catalogue = {"pass_valid_until": {"text", "date"}}
    assert validate_mapping_rules([rule("pass_valid_until")], catalogue) == []
    assert (
        validate_mapping_rules([rule("pass_valid_until", ValueType.DATE)], catalogue)
        == []
    )
    problems = validate_mapping_rules(
        [rule("pass_valid_until", ValueType.IMAGE)], catalogue
    )
    assert any("pass_valid_until" in p for p in problems)


def test_the_problem_names_every_type_the_field_allows():
    """A rejection has to say what WOULD have worked, not only what did not."""
    problems = validate_mapping_rules(
        [rule("valid_until", ValueType.IMAGE)], {"valid_until": {"text", "date"}}
    )
    assert len(problems) == 1
    assert "date" in problems[0] and "text" in problems[0] and "image" in problems[0]
