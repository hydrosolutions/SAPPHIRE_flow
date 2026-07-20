"""observation_versions archive table (Plan 035 Task 3)

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-20

Archives the (value, producing-curve) of a rating-curve-derived observation before
Flow 12 Branch A overwrites it during a rating-curve reprocessing (Task 5 wires the
writer; this task only builds the table + store).

* `UNIQUE (observation_id, rating_curve_id)` makes archival idempotent — a retry
  re-archiving the same observation/curve is a no-op (store uses ON CONFLICT DO
  NOTHING).
* Composite FK `(observation_id, station_id) -> observations(id, station_id)`
  DB-trusts the denormalised `station_id` (needs a new `UNIQUE(id, station_id)` on
  observations). Composite FKs on both `rating_curve_id` and `superseded_by_curve_id`
  enforce same-station curves (target `uq_rating_curves_id_station`, added in 0035).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Composite target so observation_versions can FK (observation_id, station_id).
    op.create_unique_constraint(
        "uq_observations_id_station", "observations", ["id", "station_id"]
    )

    op.create_table(
        "observation_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("observation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("station_id", UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "superseded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("superseded_by_curve_id", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint(
            "observation_id",
            "rating_curve_id",
            name="uq_observation_versions_obs_curve",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "station_id"],
            ["observations.id", "observations.station_id"],
            name="fk_observation_versions_observation_station",
        ),
        sa.ForeignKeyConstraint(
            ["station_id", "rating_curve_id"],
            ["rating_curves.station_id", "rating_curves.id"],
            name="fk_observation_versions_rating_curve_station",
        ),
        sa.ForeignKeyConstraint(
            ["station_id", "superseded_by_curve_id"],
            ["rating_curves.station_id", "rating_curves.id"],
            name="fk_observation_versions_superseding_curve_station",
        ),
    )
    op.create_index(
        "ix_observation_versions_station_param_ts_curve",
        "observation_versions",
        ["station_id", "parameter", "timestamp", "rating_curve_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_observation_versions_station_param_ts_curve",
        table_name="observation_versions",
    )
    op.drop_table("observation_versions")
    op.drop_constraint(
        "uq_observations_id_station", "observations", type_="unique"
    )
