"""Add supporting indexes for Phase 2 store implementations

- ix_station_group_members_station_id: enables artifact group fallback lookup
- ix_skill_scores_station_freshness: enables efficient mark_stale updates

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-24

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_station_group_members_station_id",
        "station_group_members",
        ["station_id"],
    )
    op.create_index(
        "ix_skill_scores_station_freshness",
        "skill_scores",
        ["station_id", "freshness", "eval_period_start", "eval_period_end"],
        postgresql_where="freshness = 'current'",
    )


def downgrade() -> None:
    op.drop_index("ix_skill_scores_station_freshness", "skill_scores")
    op.drop_index(
        "ix_station_group_members_station_id", "station_group_members"
    )
