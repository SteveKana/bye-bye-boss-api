"""add region and is_full_remote to job_offers

Revision ID: a7c5e1f4b6d3
Revises: b3a8f0c9d21e
Create Date: 2026-09-21 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c5e1f4b6d3"
down_revision: str | None = "b3a8f0c9d21e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_offers",
        sa.Column("region", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        op.f("ix_job_offers_region"), "job_offers", ["region"], unique=False
    )
    op.add_column(
        "job_offers",
        sa.Column(
            "is_full_remote",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # server_default above was only to backfill existing rows without a
    # table rewrite/lock on the (potentially large) job_offers table; drop
    # it afterwards so the column's real default lives in the model
    # (BaseModel/JobOffer), not silently in the schema.
    op.alter_column("job_offers", "is_full_remote", server_default=None)
    # Both new columns are recomputed for every offer on its next ingestion
    # sync regardless of whether the offer's content changed (see
    # OffersIngestionService._upsert, which always writes them) -- no
    # separate backfill needed here, existing offers just show a correct
    # région/full-remote flag after the next hourly sync (or immediately via
    # `python -m app.cli sync-offers`).


def downgrade() -> None:
    op.drop_column("job_offers", "is_full_remote")
    op.drop_index(op.f("ix_job_offers_region"), table_name="job_offers")
    op.drop_column("job_offers", "region")
