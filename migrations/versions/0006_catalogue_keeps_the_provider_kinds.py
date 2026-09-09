"""the cached catalogue keeps the provider's field kinds

`data_provider` describes a field by what it is good for -- a list of kinds --
and a field regularly carries several: `pass_valid_until` is STRING, TEXT and
DATETIME at once. The cache stored only the single `value_type` this service
derives from them, so a mapping rule could be validated against one type only,
and binding that field as a date OR as text -- both correct -- meant one of the
two was rejected.

NULLABLE, NO SERVER DEFAULT. A row cached before this column existed has no
kinds, and the reader falls back to its primary `value_type` for those -- the
same answer the old check gave. `data_field` is a pure cache that
`POST /fields/refresh` deletes and rewrites in full, so the empty column fills
itself on the next refresh; nothing has to be backfilled.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_field",
        sa.Column("kinds", sa.ARRAY(sa.String()), nullable=True),
        schema="pass_builder",
    )


def downgrade() -> None:
    op.drop_column("data_field", "kinds", schema="pass_builder")
