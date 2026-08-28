"""wallet_type carries the shared, fine-grained vocabulary

The enum held the coarse provider axis -- ``apple``, ``google``, ``samsung`` -- which
cannot say whether an Apple pass is VAS, Access or Identity. It now holds
``edutap.data_models.vocabulary.WalletType``, the vocabulary the rest of the estate
already speaks.

THE OLD VALUES ARE MAPPED, NOT DROPPED, even though this service has no rows today:
a migration that only works on an empty table is a trap for whoever runs it on one
that is not. The mapping is a READING and is written out here so it can be argued
with -- ``apple`` meant VAS, because VAS is the only Apple technology this service
ever built, and the same for Smart Tap on the Google side. Samsung was never built at
all; its reading is the analogous one and has never been exercised.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The members of `edutap.data_models.vocabulary.WalletType`, written out.
#:
#: NOT imported from the package: a migration describes the schema as it was at this
#: point in history. Importing the enum would make an old migration change its meaning
#: the day somebody adds a member -- and then replaying history would no longer
#: reproduce the database that history produced.
_NEW_VALUES = (
    "GOOGLE_ST",
    "GOOGLE_ACCESS",
    "GOOGLE_IDENTITY",
    "APPLE_VAS",
    "APPLE_ACCESS",
    "APPLE_IDENTITY",
    "SAMSUNG_ST",
    "SAMSUNG_ACCESS",
    "SAMSUNG_IDENTITY",
    "EUDI_PASS",
)

_OLD_VALUES = ("apple", "google", "samsung")

#: Coarse to fine. See the module docstring: this is a reading, not a fact.
_FORWARD = {"apple": "APPLE_VAS", "google": "GOOGLE_ST", "samsung": "SAMSUNG_ST"}

#: Fine back to coarse. Total over the new members, so a downgrade cannot strand a
#: row: everything Apple becomes `apple`, and so on. EUDI has no coarse equivalent --
#: it did not exist in that vocabulary -- and a row carrying it blocks the downgrade
#: rather than being silently turned into something else.
_BACKWARD = {
    "APPLE_VAS": "apple",
    "APPLE_ACCESS": "apple",
    "APPLE_IDENTITY": "apple",
    "GOOGLE_ST": "google",
    "GOOGLE_ACCESS": "google",
    "GOOGLE_IDENTITY": "google",
    "SAMSUNG_ST": "samsung",
    "SAMSUNG_ACCESS": "samsung",
    "SAMSUNG_IDENTITY": "samsung",
}

#: Where the type is used. `audit_log.wallet_type` is nullable, `template_variant`'s
#: is not; the SQL below is the same either way.
_COLUMNS = (("template_variant", "wallet_type"), ("audit_log", "wallet_type"))


def _case(mapping: dict[str, str], column: str) -> str:
    """Render a CASE expression that rewrites one column's values."""
    arms = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in mapping.items())
    return f"CASE {column}::text {arms} END"


def _swap_enum(values: tuple[str, ...], mapping: dict[str, str]) -> None:
    """Replace the `wallet_type` enum type, rewriting every column that uses it.

    Postgres cannot add and remove members of an enum in place, so the type is
    rebuilt: park the columns on `text`, drop the type, create it afresh, cast back
    through the mapping. Done in one migration, so no state in between is visible to
    anything else.
    """
    for table, column in _COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE text")

    op.execute("DROP TYPE wallet_type")
    members = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE wallet_type AS ENUM ({members})")

    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE wallet_type "
            f"USING ({_case(mapping, column)})::wallet_type"
        )


def upgrade() -> None:
    """Coarse provider axis -> shared fine-grained vocabulary."""
    _swap_enum(_NEW_VALUES, _FORWARD)


def downgrade() -> None:
    """Back to the coarse axis.

    LOSSY BY NATURE: `APPLE_VAS` and `APPLE_ACCESS` both become `apple`, and the
    difference is gone. A row carrying `EUDI_PASS` has no coarse equivalent and makes
    the cast fail -- deliberately, because inventing one would be worse.
    """
    unmapped = sa.text(
        "SELECT count(*) FROM template_variant WHERE wallet_type::text = 'EUDI_PASS'"
    )
    if op.get_bind().execute(unmapped).scalar():
        raise RuntimeError(
            "template_variant carries EUDI_PASS rows, which the coarse vocabulary "
            "cannot express. Decide what they should become before downgrading."
        )
    _swap_enum(_OLD_VALUES, _BACKWARD)
