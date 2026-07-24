"""add is_verified to users

Revision ID: af25e1a4dc47
Revises: 54653f3d40cb
Create Date: 2026-07-24 20:22:14.163398
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "af25e1a4dc47"
down_revision: str | None = "54653f3d40cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill existing accounts as verified (grandfathered), then drop the
    # server default so new rows rely on the app default (unverified).
    op.add_column(
        "users",
        sa.Column(
            "is_verified", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("is_verified", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("is_verified")
