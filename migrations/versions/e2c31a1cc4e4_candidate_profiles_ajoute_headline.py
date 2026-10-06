"""candidate_profiles ajoute headline

Revision ID: e2c31a1cc4e4
Revises: 0fdbbebf4fd6
Create Date: 2026-09-15 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2c31a1cc4e4"
down_revision: str | None = "0fdbbebf4fd6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles", sa.Column("headline", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "headline")
