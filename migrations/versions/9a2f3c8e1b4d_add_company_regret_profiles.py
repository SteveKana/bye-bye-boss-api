"""add company_regret_profiles

Revision ID: 9a2f3c8e1b4d
Revises: f6c0ddda0194
Create Date: 2026-09-30 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a2f3c8e1b4d"
down_revision: str | None = "f6c0ddda0194"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_regret_profiles",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "company_name_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("company_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "regret_availability", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("regret_score", sa.Integer(), nullable=True),
        sa.Column("mention_count", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("company_regret_profiles", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_company_regret_profiles_id"), ["id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_company_regret_profiles_company_name_key"),
            ["company_name_key"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("company_regret_profiles", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_company_regret_profiles_company_name_key")
        )
        batch_op.drop_index(batch_op.f("ix_company_regret_profiles_id"))

    op.drop_table("company_regret_profiles")
