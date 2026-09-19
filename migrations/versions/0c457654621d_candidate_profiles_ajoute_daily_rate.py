"""candidate_profiles ajoute daily_rate

Revision ID: 0c457654621d
Revises: 8e0dbbe3c580
Create Date: 2026-09-19 11:07:11.634850
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0c457654621d"
down_revision: str | None = "8e0dbbe3c580"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles", sa.Column("daily_rate", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "daily_rate")
