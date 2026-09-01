"""Where this package's tables live, and who is allowed to look at them.

Two readers, one file. `edutap.db_definitions` reads `definition` through an
entry point to check this package against the others that share the database;
the Alembic `env.py` reads the plain constants above it. Declaring either of
them twice is how they drift.

`edutap.db_definitions` used to be a development dependency, and this import was
guarded so a deployment without the tool could still import this module --
`definition` was `None` in that case. Since 2026-09-01 the package is a runtime
dependency: `ClusterSettings` is how this service reaches the cluster at all. The
guard could no longer trigger, and a guard that cannot trigger asserts a design
that is no longer true, so it is gone.

`env.py` still reads the plain constants rather than `definition` -- not because
the tool might be absent, but because a migration needs the version table and the
owned schemas, not a description of the package for a cross-package check.
"""

from collections.abc import Callable
from typing import Any

from edutap.db_definitions import SchemaDefinition

from . import db  # noqa: F401  importing registers the tables on the metadata
from .base import metadata

PACKAGE_NAME = "edutap.pass_builder"

VERSION_TABLE = "alembic_version_pass_builder"
"""This package's migration history, named so it cannot be mistaken for another's.

Several packages share one database, which means several histories. In a shared
`alembic_version` the second package's first migration would read the first
package's revision as its own state and skip its own baseline.
"""

OWNED_SCHEMAS: frozenset[str] = frozenset(
    table.schema for table in metadata.tables.values() if table.schema
)
"""The schemas this package is responsible for.

Read off the metadata rather than written down, so it cannot disagree with where
the tables actually declare themselves.
"""

VERSION_TABLE_SCHEMA: str | None = (
    next(iter(OWNED_SCHEMAS)) if len(OWNED_SCHEMAS) == 1 else None
)
"""The schema the history table belongs in, where it can be derived.

A package that grows a second schema has to say which one holds its history, and
then this is `None` -- deliberately a value and not an exception. Importing this
module is what an entry-point scan does, so raising here would take
`edutap-dbdef` down while it was merely looking at the installed packages.
"""


def require_version_table_schema() -> str:
    """Return `VERSION_TABLE_SCHEMA`, or refuse to migrate without one.

    Alembic's fallback for a missing `version_table_schema` is the connection's
    default schema, which is `public`: the one schema the split reserves for the
    cross-package contract, and the last place several packages' histories
    should collect.
    """
    if VERSION_TABLE_SCHEMA is None:
        raise RuntimeError(
            f"{PACKAGE_NAME} holds tables in {sorted(OWNED_SCHEMAS)}. "
            f"Name the schema that holds {VERSION_TABLE!r} explicitly, here and "
            "in the SchemaDefinition's version_table_schema."
        )
    return VERSION_TABLE_SCHEMA


def include_name_for(
    default_schema: str | None,
) -> Callable[[str | None, str, dict], bool]:
    """Return Alembic's `include_name` hook, bounded to this package's schemas.

    Without it, `include_schemas=True` shows autogenerate every table in the
    shared database, and it proposes dropping every one it does not find in this
    package's metadata -- `public.person_view`, `binding.*`, and whatever else
    the site happens to run. Applied unread, such a migration deletes other
    services' data. The bound is therefore the condition for autogenerate being
    usable here at all, not a refinement of it.

    Bounded by *schema*, not by known table name: a table inside `pass_builder`
    that this package does not declare is drift in something this package owns,
    and autogenerate should say so.

    `default_schema` is the connection's own default schema, read from
    `connection.dialect.default_schema_name` rather than assumed to be `public`.
    Alembic passes `None` for whichever schema that is, so both the schema's own
    name and a table's `parent_names["schema_name"]` are normalised through it.
    """

    def include_name(
        name: str | None, type_: str, parent_names: dict[str, Any]
    ) -> bool:
        if type_ == "schema":
            return (name or default_schema) in OWNED_SCHEMAS
        if type_ == "table":
            schema = parent_names.get("schema_name") or default_schema
            if schema not in OWNED_SCHEMAS:
                return False
            # Alembic's own history table is not in the metadata, so anything
            # that reflects it proposes dropping it. Alembic drops it from the
            # comparison itself -- but only where `schema_name` equals the
            # configured `version_table_schema`, and it passes `None` for a
            # schema that is the connection's default. Excluding it here does
            # not depend on which schema the connection calls default.
            return not (schema == VERSION_TABLE_SCHEMA and name == VERSION_TABLE)
        return True

    return include_name


definition = SchemaDefinition(
    name=PACKAGE_NAME,
    metadata=metadata,
    version_table=VERSION_TABLE,
)
