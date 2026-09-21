"""add cv_analyzed_at to candidate_profiles

Revision ID: b3a8f0c9d21e
Revises: ff0f86e6eba2
Create Date: 2026-09-21 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3a8f0c9d21e"
down_revision: str | None = "ff0f86e6eba2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("cv_analyzed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: for every existing profile, `created_at` IS the moment of its
    # first (and for most candidates, only) CV import -- see
    # service.import_cv, which is the only place that creates a row. Without
    # this, every existing candidate would see a blank "Dernière mise à
    # jour" until their next CV re-upload.
    op.execute(
        "UPDATE candidate_profiles SET cv_analyzed_at = created_at "
        "WHERE cv_analyzed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "cv_analyzed_at")
