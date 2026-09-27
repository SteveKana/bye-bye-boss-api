"""add verification_completed_at to candidate_profiles

Revision ID: e35f252f6205
Revises: 8acb491de927
Create Date: 2026-09-27 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e35f252f6205"
down_revision: str | None = "8acb491de927"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("verification_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Light backfill for profiles that already finished onboarding before
    # this column existed -- not required for correctness (the frontend
    # guard checks status == "complete" first, precisely so a null here
    # never bounces an already-finished profile into the wizard), but keeps
    # the column meaningful for every complete profile rather than only new
    # ones. updated_at is the best available proxy for "when this profile
    # was last confirmed" on old rows; a fresh, real timestamp is written by
    # CvService.apply_verification going forward.
    op.execute(
        """
        UPDATE candidate_profiles
        SET verification_completed_at = updated_at
        WHERE status = 'complete' AND verification_completed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "verification_completed_at")
