from edutap.pass_builder.engine.placeholders import (
    resolve_placeholders,
    scan_placeholders,
)


def test_scan_returns_pointer_and_field_for_each_placeholder():
    obj = {"header": {"value": "${person.name}"}, "sub": [{"value": "${person.id}"}]}
    assert scan_placeholders(obj) == [
        ("/header/value", "person.name"),
        ("/sub/0/value", "person.id"),
    ]


def test_resolve_replaces_string_values_only():
    obj = {"value": "${person.name}", "person.name": "not-a-placeholder"}
    result = resolve_placeholders(obj, {"person.name": "Ada"})
    assert result["value"] == "Ada"
    assert result["person.name"] == "not-a-placeholder"


def test_dollar_dollar_is_an_escape_for_a_literal_dollar():
    obj = {"value": "price $$5"}
    assert resolve_placeholders(obj, {})["value"] == "price $5"


def test_placeholder_inside_surrounding_text_is_substituted():
    obj = {"value": "Hello ${person.name}!"}
    assert resolve_placeholders(obj, {"person.name": "Ada"})["value"] == "Hello Ada!"


def test_keys_are_never_touched():
    obj = {"${person.name}": "value"}
    assert list(resolve_placeholders(obj, {"person.name": "x"})) == ["${person.name}"]
