"""merge heads mark-applied et tjm

Revision ID: 32a3f38b5e87
Revises: c4f7a2d9e6b1, d8e1c4a7f2b5
Create Date: 2026-09-22 20:06:53.145684
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "32a3f38b5e87"
down_revision: str | None = ("c4f7a2d9e6b1", "d8e1c4a7f2b5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
