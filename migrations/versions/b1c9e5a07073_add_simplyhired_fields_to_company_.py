"""add simplyhired fields to company_regret_profiles

Revision ID: b1c9e5a07073
Revises: 9a2f3c8e1b4d
Create Date: 2026-09-30 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b1c9e5a07073"
down_revision: str | None = "9a2f3c8e1b4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_json_variant = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "company_regret_profiles", sa.Column("overall_rating", sa.Float(), nullable=True)
    )
    op.add_column(
        "company_regret_profiles",
        sa.Column(
            "category_scores", _json_variant, nullable=False, server_default="{}"
        ),
    )
    op.add_column(
        "company_regret_profiles",
        sa.Column("satisfaction_percent", sa.Integer(), nullable=True),
    )
    op.add_column(
        "company_regret_profiles",
        sa.Column("source_url", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("company_regret_profiles", "source_url")
    op.drop_column("company_regret_profiles", "satisfaction_percent")
    op.drop_column("company_regret_profiles", "category_scores")
    op.drop_column("company_regret_profiles", "overall_rating")
