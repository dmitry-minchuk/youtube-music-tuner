"""graph expansion and wave freshness

Adds the hop distance that turns the candidate pool into a real graph, two
lookup indexes for frontier expansion, and the freshness counters recorded on
every generation.

Revision ID: b7c41d92e5a8
Revises: ad53479ba506
Create Date: 2026-08-01 18:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c41d92e5a8"
down_revision: str | None = "ad53479ba506"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("candidate_edges", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hop", sa.Integer(), nullable=False, server_default="1"))
        batch_op.create_index("ix_candidate_seed", ["seed_video_id"], unique=False)
        batch_op.create_index("ix_candidate_target", ["candidate_video_id"], unique=False)

    with op.batch_alter_table("queue_generations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "overlap_previous_percent", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column("pool_size", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("queue_generations", schema=None) as batch_op:
        batch_op.drop_column("pool_size")
        batch_op.drop_column("overlap_previous_percent")

    with op.batch_alter_table("candidate_edges", schema=None) as batch_op:
        batch_op.drop_index("ix_candidate_target")
        batch_op.drop_index("ix_candidate_seed")
        batch_op.drop_column("hop")
