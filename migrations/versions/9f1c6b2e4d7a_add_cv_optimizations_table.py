"""add cv_optimizations table

Revision ID: 9f1c6b2e4d7a
Revises: 32a3f38b5e87
Create Date: 2026-09-22 20:13:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9f1c6b2e4d7a"
down_revision: str | None = "32a3f38b5e87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cv_optimizations",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_match_id", sa.Uuid(), nullable=False),
        sa.Column("headline", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary_why", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "experiences",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "skills",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql"),
            nullable=False,
        ),
        sa.Column("advice", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cv_optimizations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cv_optimizations_id"), ["id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_cv_optimizations_candidate_match_id"),
            ["candidate_match_id"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("cv_optimizations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cv_optimizations_candidate_match_id"))
        batch_op.drop_index(batch_op.f("ix_cv_optimizations_id"))

    op.drop_table("cv_optimizations")
