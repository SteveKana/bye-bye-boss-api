"""candidate_profiles disponibilite structuree (4 etats)

Revision ID: 0fdbbebf4fd6
Revises: 1c5cb62b7878
Create Date: 2026-09-14 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0fdbbebf4fd6"
down_revision: str | None = "1c5cb62b7878"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "availability_status",
            sa.String(),
            nullable=False,
            server_default="immediate",
        ),
    )
    op.add_column(
        "candidate_profiles", sa.Column("availability_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("notice_period_months", sa.Integer(), nullable=True),
    )
    op.drop_column("candidate_profiles", "availability")


def downgrade() -> None:
    op.add_column(
        "candidate_profiles", sa.Column("availability", sa.String(), nullable=True)
    )
    op.drop_column("candidate_profiles", "notice_period_months")
    op.drop_column("candidate_profiles", "availability_date")
    op.drop_column("candidate_profiles", "availability_status")
