"""skill label glossary + candidate_matches.labels_done

Revision ID: b8c4d2e6f1a3
Revises: a7b3c9d1e5f2
Create Date: 2026-10-04 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c4d2e6f1a3"
down_revision: str | None = "a7b3c9d1e5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("label", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_skill_labels_id"), "skill_labels", ["id"])
    op.create_index(op.f("ix_skill_labels_key"), "skill_labels", ["key"], unique=True)

    # Every existing match still has to go through the glossary once.
    op.add_column(
        "candidate_matches",
        sa.Column(
            "labels_done", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        op.f("ix_candidate_matches_labels_done"), "candidate_matches", ["labels_done"]
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_candidate_matches_labels_done"), table_name="candidate_matches"
    )
    op.drop_column("candidate_matches", "labels_done")
    op.drop_index(op.f("ix_skill_labels_key"), table_name="skill_labels")
    op.drop_index(op.f("ix_skill_labels_id"), table_name="skill_labels")
    op.drop_table("skill_labels")
