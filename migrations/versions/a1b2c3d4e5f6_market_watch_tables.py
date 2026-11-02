"""market watch: per-offer tags, per-profile tag, weekly counts

Revision ID: a1b2c3d4e5f6
Revises: f7a9c1e3b5d6
Create Date: 2026-11-02 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f7a9c1e3b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "offer_market_tags",
        *_base_columns(),
        sa.Column("job_offer_id", sa.Uuid(), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("specialty", sa.String(), nullable=True),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["job_offer_id"], ["job_offers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offer_market_tags_id", "offer_market_tags", ["id"])
    op.create_index(
        "ix_offer_market_tags_job_offer_id",
        "offer_market_tags",
        ["job_offer_id"],
        unique=True,
    )
    op.create_index("ix_offer_market_tags_family", "offer_market_tags", ["family"])

    op.create_table(
        "profile_market_tags",
        *_base_columns(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("specialty", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_profile_market_tags_id", "profile_market_tags", ["id"])
    op.create_index(
        "ix_profile_market_tags_profile_id",
        "profile_market_tags",
        ["profile_id"],
        unique=True,
    )

    op.create_table(
        "market_group_weekly",
        *_base_columns(),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("specialty", sa.String(), nullable=False),
        sa.Column("offers_total", sa.Integer(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_start", "family", "specialty", name="uq_market_group_weekly"
        ),
    )
    op.create_index("ix_market_group_weekly_id", "market_group_weekly", ["id"])
    op.create_index(
        "ix_market_group_weekly_week_start", "market_group_weekly", ["week_start"]
    )

    op.create_table(
        "market_skill_weekly",
        *_base_columns(),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("specialty", sa.String(), nullable=False),
        sa.Column("skill_key", sa.String(), nullable=False),
        sa.Column("skill_label", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("offers_with_skill", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_start",
            "family",
            "specialty",
            "skill_key",
            name="uq_market_skill_weekly",
        ),
    )
    op.create_index("ix_market_skill_weekly_id", "market_skill_weekly", ["id"])
    op.create_index(
        "ix_market_skill_weekly_week_start", "market_skill_weekly", ["week_start"]
    )


def downgrade() -> None:
    op.drop_table("market_skill_weekly")
    op.drop_table("market_group_weekly")
    op.drop_table("profile_market_tags")
    op.drop_table("offer_market_tags")
