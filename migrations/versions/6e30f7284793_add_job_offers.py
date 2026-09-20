"""add job_offers

Revision ID: 6e30f7284793
Revises: c8bb5e3d8f9e
Create Date: 2026-09-20 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6e30f7284793"
down_revision: str | None = "c8bb5e3d8f9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_offers",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("external_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("company_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("location", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("contract_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("remote_policy", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_label", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        # JSONB on Postgres, plain JSON elsewhere -- matches
        # app.modules.offers.models._JsonColumn (JSON().with_variant(JSONB())).
        sa.Column(
            "raw",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "external_id", name="uq_job_offers_source_external_id"
        ),
    )
    with op.batch_alter_table("job_offers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_job_offers_id"), ["id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_job_offers_source"), ["source"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_job_offers_external_id"), ["external_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("job_offers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_offers_external_id"))
        batch_op.drop_index(batch_op.f("ix_job_offers_source"))
        batch_op.drop_index(batch_op.f("ix_job_offers_id"))

    op.drop_table("job_offers")
