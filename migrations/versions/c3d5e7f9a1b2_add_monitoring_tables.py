"""admin monitoring: page views, AI usage, incidents, announcements

Revision ID: c3d5e7f9a1b2
Revises: e1a9c3d5b7f2
Create Date: 2026-10-06 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: str | None = "e1a9c3d5b7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _text() -> sa.types.TypeEngine:
    return sqlmodel.sql.sqltypes.AutoString()


def upgrade() -> None:
    op.create_table(
        "monitoring_page_views",
        *_base(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("path", _text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_page_views_id", "monitoring_page_views", ["id"])
    op.create_index(
        "ix_monitoring_page_views_user_id", "monitoring_page_views", ["user_id"]
    )
    op.create_index("ix_monitoring_page_views_path", "monitoring_page_views", ["path"])

    op.create_table(
        "monitoring_ai_usage",
        *_base(),
        sa.Column("stage", _text(), nullable=False),
        sa.Column("model", _text(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_ai_usage_id", "monitoring_ai_usage", ["id"])
    op.create_index("ix_monitoring_ai_usage_stage", "monitoring_ai_usage", ["stage"])

    op.create_table(
        "monitoring_incidents",
        *_base(),
        sa.Column("fingerprint", _text(), nullable=False),
        sa.Column("kind", _text(), nullable=False),
        sa.Column("title", _text(), nullable=False),
        sa.Column("context", _text(), nullable=False),
        sa.Column("origin", _text(), nullable=False),
        sa.Column("technical_cause", _text(), nullable=False),
        sa.Column("user_message", _text(), nullable=True),
        sa.Column("status", _text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("user_ids", _JSON, nullable=True),
        sa.Column("occurrences", _JSON, nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_incidents_id", "monitoring_incidents", ["id"])
    op.create_index(
        "ix_monitoring_incidents_fingerprint",
        "monitoring_incidents",
        ["fingerprint"],
        unique=True,
    )
    op.create_index("ix_monitoring_incidents_kind", "monitoring_incidents", ["kind"])
    op.create_index(
        "ix_monitoring_incidents_status", "monitoring_incidents", ["status"]
    )

    op.create_table(
        "monitoring_announcements",
        *_base(),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("subject", _text(), nullable=False),
        sa.Column("body", _text(), nullable=False),
        sa.Column("content_hash", _text(), nullable=False),
        sa.Column("audience", _text(), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False),
        sa.Column("skipped_unsubscribed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monitoring_announcements_id", "monitoring_announcements", ["id"])
    op.create_index(
        "ix_monitoring_announcements_admin_id", "monitoring_announcements", ["admin_id"]
    )
    op.create_index(
        "ix_monitoring_announcements_content_hash",
        "monitoring_announcements",
        ["content_hash"],
    )
    op.create_index(
        "ix_monitoring_announcements_is_test", "monitoring_announcements", ["is_test"]
    )

    op.create_table(
        "monitoring_announcement_optouts",
        *_base(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_monitoring_announcement_optouts_id", "monitoring_announcement_optouts", ["id"]
    )
    op.create_index(
        "ix_monitoring_announcement_optouts_user_id",
        "monitoring_announcement_optouts",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("monitoring_announcement_optouts")
    op.drop_table("monitoring_announcements")
    op.drop_table("monitoring_incidents")
    op.drop_table("monitoring_ai_usage")
    op.drop_table("monitoring_page_views")
