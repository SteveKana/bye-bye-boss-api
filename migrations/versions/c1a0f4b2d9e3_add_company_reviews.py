"""add company_reviews

Revision ID: c1a0f4b2d9e3
Revises: b1c9e5a07073
Create Date: 2026-10-02 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a0f4b2d9e3"
down_revision: str | None = "b1c9e5a07073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_reviews",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "company_name_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("overall_rating", sa.Float(), nullable=True),
        sa.Column("job_title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("location", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("review_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("pros", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cons", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source_url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("company_reviews", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_company_reviews_id"), ["id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_company_reviews_company_name_key"),
            ["company_name_key"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("company_reviews", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_company_reviews_company_name_key"))
        batch_op.drop_index(batch_op.f("ix_company_reviews_id"))

    op.drop_table("company_reviews")
