"""add candidate_matches

Revision ID: 5715ddb7f012
Revises: 6e30f7284793
Create Date: 2026-09-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5715ddb7f012"
down_revision: str | None = "6e30f7284793"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidate_matches",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_profile_id", sa.Uuid(), nullable=False),
        sa.Column("job_offer_id", sa.Uuid(), nullable=False),
        sa.Column("company_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("career_score", sa.Integer(), nullable=False),
        sa.Column("ats_score", sa.Integer(), nullable=False),
        sa.Column("ats_potential", sa.Integer(), nullable=False),
        sa.Column("blocking_message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "analysis",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "regret_availability", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("regret_score", sa.Integer(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_profile_id",
            "job_offer_id",
            name="uq_candidate_matches_profile_offer",
        ),
    )
    with op.batch_alter_table("candidate_matches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_candidate_matches_id"), ["id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_candidate_matches_candidate_profile_id"),
            ["candidate_profile_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_candidate_matches_job_offer_id"),
            ["job_offer_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("candidate_matches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidate_matches_job_offer_id"))
        batch_op.drop_index(batch_op.f("ix_candidate_matches_candidate_profile_id"))
        batch_op.drop_index(batch_op.f("ix_candidate_matches_id"))

    op.drop_table("candidate_matches")
