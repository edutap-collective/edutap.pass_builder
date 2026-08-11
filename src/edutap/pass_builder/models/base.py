"""Package-local metadata and declarative base.

The naming convention is COPIED from `edutap.db_definitions`, deliberately not
imported: importing would give this deployed service a runtime dependency on a
tool that is never deployed. `edutap-dbdef check` verifies that every package
uses the same convention, which is what keeps the copies honest.

The metadata is package-local because `SQLModel.metadata` is a process-wide
singleton. Five packages share one database here, and a generator that cannot
tell them apart cannot order, split or diff them -- `create_all` on the shared
singleton creates every *other* imported package's tables as well, and
autogenerate proposes dropping every table it does not recognise, which for a
shared database means every other service's data.
"""

from sqlalchemy import MetaData
from sqlmodel import SQLModel

NAMING_CONVENTION: dict[str, str] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

SCHEMA = "pass_builder"
"""The one schema this package owns.

Written down once here rather than repeated in every `__table_args__`, so that
`dbdef.OWNED_SCHEMAS` and the tables cannot drift apart. The name is not a free
choice -- it is the one the estate's schema split assigns to this package.
"""

metadata = MetaData(naming_convention=NAMING_CONVENTION, schema=SCHEMA)
"""The metadata every table of this package registers on.

`schema=` here rather than `__table_args__ = {"schema": ...}` on each of the
eleven tables, and that is not only brevity. A schema set on the MetaData is
also the resolution context for *string* foreign keys: `foreign_key="tenant.id"`
then resolves to `pass_builder.tenant.id` without every reference having to
spell the schema out. Written per table instead, each of the eleven would be one
more place to forget -- and a forgotten one does not fail loudly, it silently
resolves through `search_path` to wherever the connecting role happens to look.
"""


class Base(SQLModel):
    """Declarative base binding this package's tables to its own metadata.

    A SQLModel subclass that carries its own `metadata` attribute registers its
    tables exclusively there, leaving the global `SQLModel.metadata` untouched.
    Every table in `db.py` inherits from this instead of from `SQLModel`.
    """

    metadata = metadata
