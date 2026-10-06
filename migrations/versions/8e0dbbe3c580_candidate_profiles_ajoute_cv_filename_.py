"""candidate_profiles ajoute cv_filename et cv_content_type

Revision ID: 8e0dbbe3c580
Revises: e2c31a1cc4e4
Create Date: 2026-09-16 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e0dbbe3c580"
down_revision: str | None = "e2c31a1cc4e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles", sa.Column("cv_filename", sa.String(), nullable=True)
    )
    op.add_column(
        "candidate_profiles", sa.Column("cv_content_type", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "cv_content_type")
    op.drop_column("candidate_profiles", "cv_filename")
