"""add application_status to candidate_matches

Revision ID: b7e4a9c1f3d2
Revises: a7c5e1f4b6d3
Create Date: 2026-09-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4a9c1f3d2"
down_revision: str | None = "a7c5e1f4b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_matches",
        sa.Column(
            "application_status",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="not_applied",
        ),
    )
    # server_default above was only to backfill every existing row (every
    # match computed before this feature shipped has no declared
    # application -- "not_applied" is exactly right for them); drop it
    # afterwards so the column's real default lives in the model
    # (CandidateMatch), not silently in the schema, same convention as
    # a7c5e1f4b6d3's is_full_remote.
    op.alter_column("candidate_matches", "application_status", server_default=None)
    op.create_index(
        op.f("ix_candidate_matches_application_status"),
        "candidate_matches",
        ["application_status"],
        unique=False,
    )
    op.add_column(
        "candidate_matches",
        sa.Column(
            "application_status_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_matches", "application_status_updated_at")
    op.drop_index(
        op.f("ix_candidate_matches_application_status"),
        table_name="candidate_matches",
    )
    op.drop_column("candidate_matches", "application_status")
