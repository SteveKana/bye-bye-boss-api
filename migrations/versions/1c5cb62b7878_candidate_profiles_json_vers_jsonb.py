"""candidate_profiles JSON vers JSONB

Revision ID: 1c5cb62b7878
Revises: d101450e259c
Create Date: 2026-09-13 17:43:48.585548
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1c5cb62b7878"
down_revision: str | None = "d101450e259c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The 6 list-shaped columns on candidate_profiles that were stored as plain
# JSON. JSONB is Postgres-only, more compact, and indexable -- worth doing
# now, before the matching engine starts querying on skills/experiences.
_COLUMNS = (
    "experiences",
    "skills",
    "formations",
    "languages",
    "certifications",
    "contract_types",
    "remote_preferences",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.alter_column(
            "candidate_profiles",
            column,
            type_=postgresql.JSONB(astext_type=None),
            postgresql_using=f"{column}::jsonb",
        )


def downgrade() -> None:
    for column in _COLUMNS:
        op.alter_column(
            "candidate_profiles",
            column,
            type_=postgresql.JSON(astext_type=None),
            postgresql_using=f"{column}::json",
        )
