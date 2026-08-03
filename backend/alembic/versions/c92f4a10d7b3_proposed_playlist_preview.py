"""proposed playlist preview

Stores the reviewed track list on the manifest so Publish writes exactly what
Preview showed, and so Regenerate can replace it with another candidate.

Revision ID: c92f4a10d7b3
Revises: b7c41d92e5a8
Create Date: 2026-08-03 14:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c92f4a10d7b3"
down_revision: str | None = "b7c41d92e5a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("managed_playlists", schema=None) as batch_op:
        batch_op.add_column(sa.Column("proposed_desired_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("managed_playlists", schema=None) as batch_op:
        batch_op.drop_column("proposed_desired_json")
