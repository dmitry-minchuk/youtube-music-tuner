"""unknown playlist size

YouTube omits the size for its system playlists, so 0 could not be told apart
from "not reported". The column becomes nullable and the two known system
playlists are reset so the UI stops claiming they are empty.

Revision ID: d5a71c8e93f2
Revises: c92f4a10d7b3
Create Date: 2026-08-03 15:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5a71c8e93f2"
down_revision: str | None = "c92f4a10d7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("remote_playlists", schema=None) as batch_op:
        batch_op.alter_column("track_count", existing_type=sa.Integer(), nullable=True)

    op.execute(
        "UPDATE remote_playlists SET track_count = NULL "
        "WHERE track_count = 0 AND playlist_id IN ('LM', 'SE')"
    )


def downgrade() -> None:
    op.execute("UPDATE remote_playlists SET track_count = 0 WHERE track_count IS NULL")
    with op.batch_alter_table("remote_playlists", schema=None) as batch_op:
        batch_op.alter_column("track_count", existing_type=sa.Integer(), nullable=False)
