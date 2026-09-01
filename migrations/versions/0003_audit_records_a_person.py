"""audit_log can name a person, not only a machine credential

`actor_client_id` is a foreign key into `api_client`, so it can only ever name a
machine. Once the management UI exists, the actions with the highest consequence --
uploading a signing credential, publishing a version -- are performed by people, and
every one of them would have been recorded with no actor at all.

That is worse than it sounds: a NULL there is indistinguishable from an entry whose
actor was never captured, so the audit log would have looked complete while saying
nothing about who did the one thing worth asking about later.

A second column rather than a wider first one, because the foreign key is worth
keeping for the machine case: it is what makes `actor_client_id` answer "which
service" rather than "some string somebody wrote".

NO CHECK CONSTRAINT REQUIRING EXACTLY ONE. `actor_client_id` has been nullable since
0001, and this migration is not the place to decide what a pre-existing NULL means.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "audit_log"
_COLUMN = "actor_principal"
_INDEX = "ix_audit_log_actor_principal"


def upgrade() -> None:
    """Add the person actor and an index for asking "what did X do"."""
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))
    op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    """Drop it again.

    LOSSY: every management action a person performed loses its actor. The rows
    stay, and they then look like the entries this column was added to stop
    producing.
    """
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
