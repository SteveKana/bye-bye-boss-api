"""add mobility_region to candidate_profiles

Revision ID: ff0f86e6eba2
Revises: ad2d9912eeed
Create Date: 2026-09-20 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff0f86e6eba2"
down_revision: str | None = "ad2d9912eeed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("mobility_region", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "mobility_region")
