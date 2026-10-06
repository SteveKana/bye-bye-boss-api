"""add daily_rate_min/daily_rate_max to job_offers

Revision ID: d8e1c4a7f2b5
Revises: b7e4a9c1f3d2
Create Date: 2026-09-22 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e1c4a7f2b5"
down_revision: str | None = "b7e4a9c1f3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_offers", sa.Column("daily_rate_min", sa.Integer(), nullable=True)
    )
    op.add_column(
        "job_offers", sa.Column("daily_rate_max", sa.Integer(), nullable=True)
    )
    # No backfill step here: every existing offer gets these columns
    # populated the next time OffersIngestionService._upsert refreshes it
    # (see service.py's `values` dict, always written on update, not just
    # on create) -- the regular hourly sync does this on its own, same as
    # every other field this ingestion pipeline derives from source text.


def downgrade() -> None:
    op.drop_column("job_offers", "daily_rate_max")
    op.drop_column("job_offers", "daily_rate_min")
