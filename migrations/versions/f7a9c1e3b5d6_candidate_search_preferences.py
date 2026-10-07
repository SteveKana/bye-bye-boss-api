"""candidate search preferences: several régions, unknown-région flag, saved-at

Revision ID: f7a9c1e3b5d6
Revises: e5f7a9b1c3d4
Create Date: 2026-10-07 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7a9c1e3b5d6"
down_revision: str | None = "e5f7a9b1c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "mobility_regions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "include_unknown_region",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # NULL for every existing account on purpose: preferences only filter the
    # daily offers once saved (see CandidateProfile.preferences_saved_at).
    op.add_column(
        "candidate_profiles",
        sa.Column("preferences_saved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "preferences_saved_at")
    op.drop_column("candidate_profiles", "include_unknown_region")
    op.drop_column("candidate_profiles", "mobility_regions")
