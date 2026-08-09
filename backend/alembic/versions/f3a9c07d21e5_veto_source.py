"""veto source

Distinguishes the veto button (MANUAL) from the farm detector (FARM_AUTO)
and remembers an overridden auto-veto (OVERRIDDEN) so the detector never
fights an explicit human decision (docs/05 section 4, docs/07 section 2).

Revision ID: f3a9c07d21e5
Revises: e8b204f1c6d7
Create Date: 2026-08-09 12:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9c07d21e5"
down_revision: str | None = "e8b204f1c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("taste_vetoes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="MANUAL")
        )


def downgrade() -> None:
    with op.batch_alter_table("taste_vetoes", schema=None) as batch_op:
        batch_op.drop_column("source")
