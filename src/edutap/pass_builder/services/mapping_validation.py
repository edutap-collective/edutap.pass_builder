"""Validate mapping rules against the cached data-provider catalogue."""

from ..engine.spec import RuleSpec


def validate_mapping_rules(
    rules: list[RuleSpec], catalogue: dict[str, set[str]]
) -> list[str]:
    """Return a list of problems; an empty list means the rule set is valid.

    `catalogue` maps a data-provider field key to EVERY value type that field
    may be bound as, as cached in the `DataField` table. It is a set and not a
    single type because the provider describes a field by its kinds, and a
    field is regularly good for more than one thing: `pass_valid_until` arrives
    as STRING, TEXT and DATETIME, and binding it as text or as a date are both
    correct. A check against one type would reject one of the two, and no
    choice of "the" type is right for every field.

    Each rule is checked for two failure modes: the field is unknown to the
    catalogue, or it is known but bound as a type none of its kinds allow.
    """
    problems: list[str] = []
    for rule in rules:
        accepted = catalogue.get(rule.source_field)
        if accepted is None:
            problems.append(f"unknown field: {rule.source_field}")
            continue
        if rule.value_type.value not in accepted:
            allowed = ", ".join(sorted(accepted))
            problems.append(
                f"type mismatch for {rule.source_field}: "
                f"catalogue allows {allowed}, rule says {rule.value_type.value}"
            )
    return problems
