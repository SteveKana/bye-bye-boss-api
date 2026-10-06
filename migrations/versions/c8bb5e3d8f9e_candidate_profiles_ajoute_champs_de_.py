"""candidate_profiles ajoute champs de synthese

Revision ID: c8bb5e3d8f9e
Revises: 8e0dbbe3c580
Create Date: 2026-09-19 11:56:07.950956
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c8bb5e3d8f9e"
down_revision: str | None = "0c457654621d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres (matches the other list-shaped columns on this table,
# see 1c5cb62b7878), plain JSON elsewhere.
_JsonListColumn = sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("professional_summary", sa.String(), nullable=True),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("identified_roles", _JsonListColumn, nullable=True),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("domains", _JsonListColumn, nullable=True),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("skill_categories", _JsonListColumn, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "skill_categories")
    op.drop_column("candidate_profiles", "domains")
    op.drop_column("candidate_profiles", "identified_roles")
    op.drop_column("candidate_profiles", "professional_summary")
