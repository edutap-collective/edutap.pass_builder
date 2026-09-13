"""the cached catalogue belongs to a view

`Template.view_type` gave every template its own view of the data provider (0005),
but the field cache stayed one flat namespace for the whole deployment: `key` was
unique, and the reader took every row. A rule of a template reading `mensapass` was
therefore validated against whichever view the deployment happened to default to,
and answered `unknown field` for a field the provider does offer -- in the view the
template actually reads.

NULLABLE, NO SERVER DEFAULT -- the same decision `Template.view_type` states and for
the same reason: a NOT NULL column cannot be added to a table that holds rows, and a
server default would silently pin every cached field to one named view. `None` means
the deployment default, so a row written before this column existed keeps meaning
exactly what it meant.

THE UNIQUE CONSTRAINT MOVES from `key` to `(view_type, key)`. The same key may
honestly appear in more than one view, and under the old constraint the second one
could not be cached at all. Postgres treats NULLs as distinct here, so two rows with
`view_type IS NULL` and the same key would both be accepted; in practice there are
none, because `refresh_catalogue` deletes the rows of the view it is refreshing
before it writes.

NOTHING IS BACKFILLED, and nothing has to be. `data_field` is a pure cache that
`POST /fields/refresh` rewrites, and that endpoint now walks every view in use.
Production held zero rows when this was written.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Wie die alte Eindeutigkeit auf `key` allein geheissen hat.
#:
#: NICHT `data_field_key_key`. Die Metadata dieses Pakets traegt eine
#: Namenskonvention (`models/base.py`: `uq_%(table_name)s_%(column_0_name)s`), also
#: heisst sie `uq_data_field_key` -- der Postgres-Vorgabename kommt hier nie zustande.
#: Ein `DROP ... IF EXISTS data_field_key_key` haette schweigend nichts getan und die
#: alte Eindeutigkeit stehen gelassen: derselbe Schluessel in zwei Views waere weiter
#: unmoeglich, und die Migration haette gruen gemeldet.
_ALT = "uq_data_field_key"

#: Der Vorgabename, den eine Datenbank tragen kann, die VOR der Konvention entstand.
#: Mitgenommen, weil das billig ist -- und weil genau eine der beiden treffen muss,
#: was unten geprueft wird.
_ALT_OHNE_KONVENTION = "data_field_key_key"

#: Der Name der neuen Eindeutigkeit. Ebenfalls aus der Konvention:
#: `uq_<tabelle>_<erste spalte>`, und die erste Spalte ist `view_type`. Weicht er
#: davon ab, tragen eine per Alembic und eine per Metadata erzeugte Datenbank
#: verschiedene Namen fuer dieselbe Bedingung -- und ein spaeteres DROP trifft je
#: nach Herkunft.
_NEU = "uq_data_field_view_type"


def upgrade() -> None:
    op.add_column(
        "data_field",
        sa.Column("view_type", sa.String(), nullable=True),
        schema="pass_builder",
    )
    for name in (_ALT, _ALT_OHNE_KONVENTION):
        op.execute(
            f"ALTER TABLE pass_builder.data_field DROP CONSTRAINT IF EXISTS {name}"
        )
    # EINE DER BEIDEN MUSSTE TREFFEN. Bleibt eine Eindeutigkeit auf `key` allein
    # stehen, ist die Migration wirkungslos -- und ohne diese Pruefung faellt das
    # erst auf, wenn das zweite View sich nicht cachen laesst.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM pg_constraint c
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
             WHERE c.conrelid = 'pass_builder.data_field'::regclass
               AND c.contype = 'u'
               AND cardinality(c.conkey) = 1
               AND a.attname = 'key'
          ) THEN
            RAISE EXCEPTION
              'data_field traegt noch eine Eindeutigkeit auf key allein -- '
              'der Name in 0007 passt nicht zu dieser Datenbank';
          END IF;
        END $$;
        """
    )
    op.create_unique_constraint(
        _NEU, "data_field", ["view_type", "key"], schema="pass_builder"
    )
    op.create_index(
        "ix_data_field_view_type_key",
        "data_field",
        ["view_type", "key"],
        schema="pass_builder",
    )


def downgrade() -> None:
    op.drop_index("ix_data_field_view_type_key", "data_field", schema="pass_builder")
    op.drop_constraint(_NEU, "data_field", schema="pass_builder")
    # Die Zeilen aller Views ausser dem Vorgabewert fallen: `key` allein muss wieder
    # eindeutig sein, und zwei Views mit demselben Schluessel machten das unmoeglich.
    # Ein Cache ist die eine Tabelle, bei der das die richtige Antwort ist -- der
    # naechste Refresh baut ihn wieder auf.
    op.execute("DELETE FROM pass_builder.data_field WHERE view_type IS NOT NULL")
    op.drop_column("data_field", "view_type", schema="pass_builder")
    op.create_unique_constraint(_ALT, "data_field", ["key"], schema="pass_builder")
