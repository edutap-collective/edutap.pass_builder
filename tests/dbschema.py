"""Create and drop this package's schema for a test database.

The tests used to call `SQLModel.metadata.create_all` directly. That stopped
working when the package moved its tables onto its own `MetaData`: the global
singleton is empty now, so `create_all` on it creates nothing and every test
fails with `relation "pass_builder.tenant" does not exist`.

Reaching for the package's own metadata is only half of it. The tables live in a
named schema, and `create_all` does not create schemas -- in the deployment
`migrations/env.py::create_owned_schemas` does that, for the same reason, before
Alembic writes its version table.

`create_all` here rather than `alembic upgrade head`: a unit test wants the shape
the models describe, not the history that produced it. The integration suite is
where the migrations themselves belong under test.
"""

from edutap.db_definitions.public.tables import PassState
from sqlalchemy import Connection, text

from edutap.pass_builder.models import db as _db  # noqa: F401  registers the tables
from edutap.pass_builder.models.base import SCHEMA, metadata


def create_schema_and_tables(connection: Connection) -> None:
    """Create the package's schema, then every table it declares.

    Plus `public.pass_state`, which this package does not own and does not
    write: the delivery path reads it to recover the person and the template
    behind a serial number. Created from the definition in
    `edutap.db_definitions` rather than from a copy here -- a second
    declaration of a table another service writes is a second truth, and it is
    the copy that goes stale.
    """
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    metadata.create_all(connection)
    # ty does not see SQLModel's generated `__table__`, the same gap that puts
    # `ty: ignore` on every column comparison in this package.
    PassState.__table__.create(  # ty: ignore[unresolved-attribute]
        connection, checkfirst=True
    )


def drop_schema_and_tables(connection: Connection) -> None:
    """Drop every table the package declares, then its schema.

    `CASCADE` on the schema takes the enum types with it. They are not tables,
    so `drop_all` leaves them behind, and a following `create_all` in the same
    database would fail with "type wallet_type already exists".
    """
    PassState.__table__.drop(  # ty: ignore[unresolved-attribute]
        connection, checkfirst=True
    )
    metadata.drop_all(connection)
    connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
