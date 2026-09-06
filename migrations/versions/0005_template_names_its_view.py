"""template names the data provider view it reads

The view used to be one setting for the whole deployment. Three tenants issue
three passes from one provider, and they do not read the same view: the student
card reads `full_view`, the canteen pass reads `mensapass`. The view belongs to
the pass.

NULLABLE, NO SERVER DEFAULT. NULL means "the deployment's default view", which
is what every existing template read until now -- so nothing changes for them.
A NOT NULL column could not be added to a table that holds rows (see 0004's
lesson on `tenant.active`), and a server default would pin every existing
template to one named view without anybody choosing it.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template",
        sa.Column("view_type", sa.String(), nullable=True),
        schema="pass_builder",
    )


def downgrade() -> None:
    op.drop_column("template", "view_type", schema="pass_builder")
