"""split full_name into first_name last_name

Revision ID: 54653f3d40cb
Revises: 47b6a179d2de
Create Date: 2026-07-24 19:53:40.882945
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "54653f3d40cb"
down_revision: str | None = "47b6a179d2de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("first_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    # Best-effort data preservation: keep the existing name in first_name.
    op.execute("UPDATE users SET first_name = full_name WHERE full_name IS NOT NULL")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("full_name")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("full_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.execute(
        "UPDATE users SET full_name = "
        "TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, ''))"
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("first_name")
        batch_op.drop_column("last_name")
