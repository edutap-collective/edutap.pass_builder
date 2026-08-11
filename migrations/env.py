import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from logging.config import fileConfig

from alembic import context
from sqlalchemy import inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_engine_from_config

from edutap.pass_builder.models import db as _db  # noqa: F401  (registers tables)
from edutap.pass_builder.models.base import metadata as package_metadata
from edutap.pass_builder.models.dbdef import (
    OWNED_SCHEMAS,
    VERSION_TABLE,
    include_name_for,
    require_version_table_schema,
)
from edutap.pass_builder.settings import get_settings

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The database URL always comes from the application's own Settings (so it
# reads the same EDUTAP_PASS_BUILDER_DATABASE_URL environment variable the
# service itself uses) rather than from a hardcoded value in alembic.ini.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# The package's OWN metadata, not the global `SQLModel.metadata`. Several
# packages share this database, and the global singleton cannot tell them
# apart: autogenerate against it proposes dropping every table it does not
# recognise, which here would mean every other service's tables.
target_metadata = package_metadata

VERSION_TABLE_SCHEMA = require_version_table_schema()

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Same history table as online, or `alembic upgrade --sql` would render
        # its bookkeeping against a table the online path never uses.
        version_table=VERSION_TABLE,
        version_table_schema=VERSION_TABLE_SCHEMA,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def create_owned_schemas(connection: Connection) -> None:
    """Create this package's schemas, before Alembic writes anything.

    `MigrationContext.run_migrations` calls `_ensure_version_table()` *before*
    it runs the first revision. On an empty database that makes
    `CREATE TABLE pass_builder.alembic_version_pass_builder` the very first
    statement -- so a `CREATE SCHEMA` inside a migration would come too late and
    the upgrade would fail with `InvalidSchemaName` before any migration code
    ran at all. The schema has to come from here.

    Asks first, and `CREATE SCHEMA IF NOT EXISTS` is not enough to make that
    unnecessary: PostgreSQL checks `CREATE` **on the database** before it checks
    whether the schema exists. Against a database provisioned the way the
    deployment intends -- a superuser creates the schema and hands it to the DDL
    role, which does not hold `CREATE ON DATABASE` -- the bare statement fails
    with `InsufficientPrivilege` against a database that is already correct.
    Asking first means the `CREATE` is only reached where the schema really is
    missing: the fresh developer database and the test container, where the role
    does have the right.
    """
    existing = set(inspect(connection).get_schema_names())
    for schema in sorted(OWNED_SCHEMAS - existing):
        # The names come from this package's own metadata, never from input.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def _set_search_path(connection: Connection, value: str) -> None:
    """Set `search_path` via `set_config`, so the value stays a bound parameter.

    `SET search_path TO ...` takes an identifier list, which would have to be
    quoted by hand -- and the value here is read back out of the database.
    """
    connection.execute(
        text("SELECT set_config('search_path', :value, false)"), {"value": value}
    )


@contextmanager
def reflection_search_path(connection: Connection) -> Iterator[None]:
    """Pin `search_path` to the default schema while Alembic reflects.

    This is a different problem from the one `include_name` solves, and both are
    needed. PostgreSQL's reflection omits the schema of everything **visible on
    the `search_path`**; Alembic's rule is narrower -- only the *default* schema
    comes back as `None`. The two coincide only when the path holds nothing but
    the default schema.

    Where they diverge, another package's tables arrive unqualified,
    `include_name` reads them as belonging to this package, finds them in no
    metadata, and autogenerate writes `op.drop_table(...)` for them -- without a
    `schema=`, so applying it resolves against the same `search_path` and
    destroys another package's data.

    `SET` is transactional in PostgreSQL, so an aborted transaction rolls it
    back by itself; a restore that fails because the block left the transaction
    in a failed state is suppressed rather than allowed to mask the original
    error.
    """
    previous = connection.exec_driver_sql("SHOW search_path").scalar()
    _set_search_path(connection, connection.dialect.default_schema_name or "")
    try:
        yield
    finally:
        with suppress(DBAPIError):
            _set_search_path(connection, previous or "")


def do_run_migrations(connection: Connection) -> None:
    create_owned_schemas(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # This package's own history, in this package's own schema. Sharing
        # `public.alembic_version` with the other packages would make the second
        # one read the first one's revision as its own state.
        version_table=VERSION_TABLE,
        version_table_schema=VERSION_TABLE_SCHEMA,
        # Required for a package that lives in a named schema -- without it
        # autogenerate compares only the default schema and proposes creating
        # every table again. Bounded by `include_name`, see dbdef.py.
        include_schemas=True,
        include_name=include_name_for(connection.dialect.default_schema_name),
    )

    with reflection_search_path(connection), context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
