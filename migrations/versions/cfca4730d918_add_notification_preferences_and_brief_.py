"""add notification preferences and brief entries

Revision ID: cfca4730d918
Revises: 9f1c6b2e4d7a
Create Date: 2026-09-23 15:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "cfca4730d918"
down_revision: str | None = "9f1c6b2e4d7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("discord_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "discord_webhook_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
        sa.Column("whatsapp_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "whatsapp_phone_number", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notification_preferences", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notification_preferences_id"), ["id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_preferences_user_id"),
            ["user_id"],
            unique=True,
        )

    op.create_table(
        "notification_brief_entries",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_match_id", sa.Uuid(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "channels_sent",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "candidate_match_id",
            name="uq_brief_entries_user_match",
        ),
    )
    with op.batch_alter_table("notification_brief_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notification_brief_entries_id"), ["id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_notification_brief_entries_user_id"),
            ["user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_notification_brief_entries_candidate_match_id"),
            ["candidate_match_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_brief_entries", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_notification_brief_entries_candidate_match_id")
        )
        batch_op.drop_index(batch_op.f("ix_notification_brief_entries_user_id"))
        batch_op.drop_index(batch_op.f("ix_notification_brief_entries_id"))
    op.drop_table("notification_brief_entries")

    with op.batch_alter_table("notification_preferences", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notification_preferences_user_id"))
        batch_op.drop_index(batch_op.f("ix_notification_preferences_id"))
    op.drop_table("notification_preferences")
