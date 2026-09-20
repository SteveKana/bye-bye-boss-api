"""add embedding to job_offers and candidate_profiles

Revision ID: ad2d9912eeed
Revises: 5715ddb7f012
Create Date: 2026-09-20 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ad2d9912eeed"
down_revision: str | None = "5715ddb7f012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, plain JSON elsewhere -- matches
# app.modules.offers.models._JsonColumn / app.modules.cv.models._JsonListColumn
# (both JSON().with_variant(JSONB())).
_embedding_column = sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


def upgrade() -> None:
    op.add_column(
        "job_offers", sa.Column("embedding", _embedding_column, nullable=True)
    )
    op.add_column(
        "candidate_profiles", sa.Column("embedding", _embedding_column, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "embedding")
    op.drop_column("job_offers", "embedding")
