"""add application_manually_corrected to candidate_matches

Revision ID: c4f7a2d9e6b1
Revises: b7e4a9c1f3d2
Create Date: 2026-09-22 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f7a2d9e6b1"
down_revision: str | None = "b7e4a9c1f3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_matches",
        sa.Column(
            "application_manually_corrected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Same convention as b7e4a9c1f3d2: server_default only backfills every
    # existing row (none of them have ever been explicitly corrected, so
    # False is exactly right), then drop it so the real default lives in
    # the model, not silently in the schema.
    op.alter_column(
        "candidate_matches", "application_manually_corrected", server_default=None
    )


def downgrade() -> None:
    op.drop_column("candidate_matches", "application_manually_corrected")
