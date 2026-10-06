"""matching pipeline: match status, pre-filter score, batches

Revision ID: a7b3c9d1e5f2
Revises: d4e8a1c6f2b7
Create Date: 2026-10-04 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b3c9d1e5f2"
down_revision: str | None = "d4e8a1c6f2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every existing row is a fully analysed match -> server default "scored".
    op.add_column(
        "candidate_matches",
        sa.Column(
            "status",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="scored",
        ),
    )
    op.add_column(
        "candidate_matches", sa.Column("prefilter_score", sa.Integer(), nullable=True)
    )
    op.add_column("candidate_matches", sa.Column("batch_id", sa.Uuid(), nullable=True))
    op.add_column(
        "candidate_matches",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "candidate_matches",
        sa.Column(
            "dashboard_first_shown_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        op.f("ix_candidate_matches_status"), "candidate_matches", ["status"]
    )
    op.create_index(
        op.f("ix_candidate_matches_batch_id"), "candidate_matches", ["batch_id"]
    )

    op.create_table(
        "matching_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("openai_batch_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_matching_batches_id"), "matching_batches", ["id"])
    op.create_index(
        op.f("ix_matching_batches_openai_batch_id"),
        "matching_batches",
        ["openai_batch_id"],
        unique=True,
    )
    op.create_index(op.f("ix_matching_batches_status"), "matching_batches", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_matching_batches_status"), table_name="matching_batches")
    op.drop_index(
        op.f("ix_matching_batches_openai_batch_id"), table_name="matching_batches"
    )
    op.drop_index(op.f("ix_matching_batches_id"), table_name="matching_batches")
    op.drop_table("matching_batches")
    op.drop_index(op.f("ix_candidate_matches_batch_id"), table_name="candidate_matches")
    op.drop_index(op.f("ix_candidate_matches_status"), table_name="candidate_matches")
    op.drop_column("candidate_matches", "dashboard_first_shown_at")
    op.drop_column("candidate_matches", "attempts")
    op.drop_column("candidate_matches", "batch_id")
    op.drop_column("candidate_matches", "prefilter_score")
    op.drop_column("candidate_matches", "status")
