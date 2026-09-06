"""tenant gains `active`: the list is declared in settings and reconciled

Tenants are deployment topology -- three of them, changing almost never, and
already named by the permission map. From now on the settings declare them and
the service reconciles the table at startup. The table stays for the foreign
keys underneath it.

A tenant that leaves the settings while it still holds rows is not deleted (a
typo would take four tables with it) and not silently kept (a tenant removed on
purpose must stop issuing). It is marked inactive. This column is that mark.

Existing rows become active: they were created on purpose, and the first
reconciliation decides what happens next from the declaration, not from here.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="pass_builder",
    )


def downgrade() -> None:
    op.drop_column("tenant", "active", schema="pass_builder")
