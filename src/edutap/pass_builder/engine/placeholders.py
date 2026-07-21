"""Resolve ${field} placeholders inside a JSON-like structure.

The syntax is deliberately minimal: ${dotted.field} is replaced by a looked-up
value, $$ is a literal dollar sign, and replacement happens in string values
only — never in dictionary keys. No filters, no expressions, no code.
"""

import re
from collections.abc import Mapping
from typing import Any

_TOKEN = re.compile(r"\$\$|\$\{([^}]+)\}")


def _walk(obj: Any, pointer: str, out: list[tuple[str, str]]) -> None:
    if isinstance(obj, str):
        for match in _TOKEN.finditer(obj):
            if match.group(1) is not None:
                out.append((pointer, match.group(1)))
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            _walk(value, f"{pointer}/{_escape(str(key))}", out)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _walk(value, f"{pointer}/{index}", out)


def _escape(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def scan_placeholders(obj: Any) -> list[tuple[str, str]]:
    """Return (json_pointer, source_field) for every ${…} in a string value."""
    out: list[tuple[str, str]] = []
    _walk(obj, "", out)
    return out


def _substitute(text: str, values: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        return values.get(match.group(1), match.group(0))

    return _TOKEN.sub(replace, text)


def resolve_placeholders(obj: Any, values: Mapping[str, str]) -> Any:
    """Return a deep copy with ${…} resolved in string values only."""
    if isinstance(obj, str):
        return _substitute(obj, values)
    if isinstance(obj, Mapping):
        return {key: resolve_placeholders(value, values) for key, value in obj.items()}
    if isinstance(obj, list):
        return [resolve_placeholders(value, values) for value in obj]
    return obj
