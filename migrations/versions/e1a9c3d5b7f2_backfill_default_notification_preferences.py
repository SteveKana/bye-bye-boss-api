"""backfill default notification preferences for existing accounts

Email alerts have been on by default since 2026-10-04, but only for accounts
created after that: older accounts got their preference row only when they
first opened the notification settings page, so anyone who never did was left
out of every automatic brief. This gives each such account the default row
(email on, Discord/WhatsApp off).

Revision ID: e1a9c3d5b7f2
Revises: b8c4d2e6f1a3
Create Date: 2026-10-05 20:00:00.000000
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a9c3d5b7f2"
down_revision: str | None = "b8c4d2e6f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Uuid()))
    table = sa.table(
        "notification_preferences",
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("user_id", sa.Uuid()),
        sa.column("email_enabled", sa.Boolean()),
        sa.column("discord_enabled", sa.Boolean()),
        sa.column("whatsapp_enabled", sa.Boolean()),
    )
    missing = bind.execute(
        sa.select(users.c.id).where(
            ~sa.exists().where(table.c.user_id == users.c.id)
        )
    ).fetchall()
    if not missing:
        return
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "created_at": now,
                "updated_at": now,
                "user_id": row[0],
                "email_enabled": True,
                "discord_enabled": False,
                "whatsapp_enabled": False,
            }
            for row in missing
        ],
    )


def downgrade() -> None:
    # Data backfill: nothing to undo (the rows are indistinguishable from
    # ones created by the signup listener).
    pass
