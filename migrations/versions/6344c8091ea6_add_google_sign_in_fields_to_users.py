"""add google sign-in fields to users

Revision ID: 6344c8091ea6
Revises: cfca4730d918
Create Date: 2026-09-27 13:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6344c8091ea6"
down_revision: str | None = "cfca4730d918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        # An account created via "Continuer avec Google" has no password at
        # all until it goes through reset-password to set one (see
        # AuthService.login_with_google).
        batch_op.alter_column(
            "password_hash",
            existing_type=sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("google_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_users_google_id"), ["google_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_google_id"))
        batch_op.drop_column("google_id")
        batch_op.alter_column(
            "password_hash",
            existing_type=sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        )
