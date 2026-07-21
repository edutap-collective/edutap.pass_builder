"""Validate mapping rules against the cached data-provider catalogue."""

from ..engine.spec import RuleSpec


def validate_mapping_rules(
    rules: list[RuleSpec], catalogue: dict[str, str]
) -> list[str]:
    """Return a list of problems; an empty list means the rule set is valid.

    `catalogue` maps a data-provider field key to its value type slug (for
    example ``"text"``), as cached in the `DataField` table. Each rule is
    checked for two failure modes: the field is unknown to the catalogue, or
    it is known but under a different value type than the rule declares.
    """
    problems: list[str] = []
    for rule in rules:
        known_type = catalogue.get(rule.source_field)
        if known_type is None:
            problems.append(f"unknown field: {rule.source_field}")
            continue
        if known_type != rule.value_type.value:
            problems.append(
                f"type mismatch for {rule.source_field}: "
                f"catalogue says {known_type}, rule says {rule.value_type.value}"
            )
    return problems
