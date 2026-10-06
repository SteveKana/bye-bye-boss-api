"""merge heads cv et auth

Revision ID: d101450e259c
Revises: 039412531390, af25e1a4dc47
Create Date: 2026-09-13 13:17:46.336673
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d101450e259c"
down_revision: str | None = ("039412531390", "af25e1a4dc47")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
