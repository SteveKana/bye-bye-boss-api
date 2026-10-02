"""add source to company_reviews

Revision ID: d4e8a1c6f2b7
Revises: c1a0f4b2d9e3
Create Date: 2026-10-02 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e8a1c6f2b7"
down_revision: str | None = "c1a0f4b2d9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_reviews",
        sa.Column(
            "source",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="simplyhired",
        ),
    )


def downgrade() -> None:
    op.drop_column("company_reviews", "source")
