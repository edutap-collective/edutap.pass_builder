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

THE UNIQUENESS MOVES from `key` to `(view_type, key)`. The same key may honestly
appear in more than one view, and under the old one the second could not be cached
at all. Postgres treats NULLs as distinct here, so two rows with `view_type IS NULL`
and the same key would both be accepted; in practice there are none, because
`refresh_catalogue` deletes the rows of the view it is refreshing before it writes.

AND THE OLD UNIQUENESS IS AN INDEX, NOT A CONSTRAINT -- which the first version of
this migration got wrong. `0001_initial` creates it as
`create_index(..., ["key"], unique=True)`, so it lives in `pg_index` and never
appears in `pg_constraint`. Dropping constraint names alone did nothing, left the
unique index standing and reported success. Corrected on 2026-09-13, after measuring
it against the production database.

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

#: WO DIE ALTE EINDEUTIGKEIT WIRKLICH LIEGT: in einem UNIQUE INDEX, nicht in einer
#: Constraint.
#:
#: `0001_initial.py` legt sie als
#: `op.create_index(op.f("ix_data_field_key"), "data_field", ["key"], unique=True)`
#: an, und SQLModels `Field(index=True, unique=True)` erzeugt dasselbe. In
#: `pg_constraint` steht so etwas NICHT -- nur in `pg_index`.
#:
#: Die erste Fassung dieser Migration suchte ausschliesslich in `pg_constraint`. Sie
#: loeschte damit zwei Constraints, die es nie gab, liess den Unique-Index stehen und
#: meldete gruen: derselbe Schluessel in zwei Views blieb unmoeglich, und niemand
#: haette es gesehen, bevor das zweite View sich nicht cachen laesst. Am 2026-09-13
#: an der Produktionsdatenbank gemessen und hier nachgezogen.
_ALT_INDEX = "ix_data_field_key"

#: Namen, unter denen die alte Eindeutigkeit als CONSTRAINT auftreten koennte. Beide
#: mitgenommen, weil es billig ist: `uq_data_field_key` aus der Namenskonvention in
#: `models/base.py`, `data_field_key_key` als Postgres-Vorgabe einer Datenbank, die
#: vor der Konvention entstand. In den Datenbanken, die wir kennen, trifft keiner von
#: beiden -- der Unique-Index oben ist der wirkliche Fall.
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
    # DER WIRKLICHE FALL: der Unique-Index aus 0001. Er faellt und entsteht sofort
    # wieder -- ohne Eindeutigkeit, denn das Modell fuehrt `key` weiterhin als
    # `Field(index=True)`. Ein blosses Loeschen wuerde den Index verlieren, den
    # `edutap-dbdef` in der Produktion eine Zeile spaeter ohnehin anlegt; die beiden
    # Wege muessen dasselbe Schema ergeben.
    op.execute(f"DROP INDEX IF EXISTS pass_builder.{_ALT_INDEX}")
    op.create_index(
        _ALT_INDEX, "data_field", ["key"], unique=False, schema="pass_builder"
    )
    # BEIDE KATALOGE, nicht nur einer. Bleibt irgendeine Eindeutigkeit auf `key`
    # allein stehen -- als Constraint ODER als Index --, ist die Migration
    # wirkungslos, und ohne diese Pruefung faellt das erst auf, wenn das zweite View
    # sich nicht cachen laesst. Genau daran ist die erste Fassung vorbeigelaufen: Sie
    # fragte nur `pg_constraint` und meldete gruen, waehrend der Unique-INDEX stand.
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
          ) OR EXISTS (
            SELECT 1
              FROM pg_index i
              JOIN pg_attribute a
                ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
             WHERE i.indrelid = 'pass_builder.data_field'::regclass
               AND i.indisunique
               AND i.indnatts = 1
               AND a.attname = 'key'
          ) THEN
            RAISE EXCEPTION
              'data_field traegt noch eine Eindeutigkeit auf key allein -- '
              'als Constraint oder als Unique-Index. 0007 ist wirkungslos geblieben.';
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
    # Zurueck in die Form, die 0001 angelegt hat: ein UNIQUE INDEX, keine Constraint.
    # Eine Constraint hier haette 0001 nicht rueckgaengig gemacht, sondern etwas
    # anderes hergestellt -- und das naechste `drop_index` haette sie nicht gefunden.
    op.execute(f"DROP INDEX IF EXISTS pass_builder.{_ALT_INDEX}")
    op.create_index(
        _ALT_INDEX, "data_field", ["key"], unique=True, schema="pass_builder"
    )
