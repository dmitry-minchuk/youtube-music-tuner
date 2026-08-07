"""taste vetoes

The "Don't Like At All" button stores a local, never-synced strong negative:
the track leaves the pool, its artist and graph neighbourhood sink in the
ranking (docs/05 section 11). A separate table because affinity aggregates
are rebuilt from sessions and cannot carry manual signals.

Revision ID: e8b204f1c6d7
Revises: d5a71c8e93f2
Create Date: 2026-08-07 16:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8b204f1c6d7"
down_revision: str | None = "d5a71c8e93f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taste_vetoes",
        sa.Column("video_id", sa.String(length=64), nullable=False),
        sa.Column("artist_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["tracks.video_id"]),
        sa.PrimaryKeyConstraint("video_id"),
    )


def downgrade() -> None:
    op.drop_table("taste_vetoes")
