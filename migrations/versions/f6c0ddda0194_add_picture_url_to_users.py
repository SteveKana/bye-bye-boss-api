"""add picture_url to users

Revision ID: f6c0ddda0194
Revises: e35f252f6205
Create Date: 2026-09-28 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6c0ddda0194"
down_revision: str | None = "e35f252f6205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("picture_url", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "picture_url")
