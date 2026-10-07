"""offers: pgvector column + trigger for fast nearest-offer search

Skipped silently when the `vector` extension is not installed on the server:
matching then keeps its previous in-memory path.

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-10-07 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.offers.vector_ddl import (
    DOWNGRADE_STATEMENTS,
    EXTENSION_AVAILABLE_SQL,
    UPGRADE_STATEMENTS,
)

# revision identifiers, used by Alembic.
revision: str = "e5f7a9b1c3d4"
down_revision: str | None = "d4e6f8a0b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if bind.execute(sa.text(EXTENSION_AVAILABLE_SQL)).first() is None:
        return
    for statement in UPGRADE_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(sa.text(statement))
