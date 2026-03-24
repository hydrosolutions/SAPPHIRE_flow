"""Add dedup constraints, query indexes, and missing columns

Addresses:
- C1: unique constraints on observations, weather_forecasts, skill_scores
- C2: parameter/units columns on forecasts and hindcast_forecasts
- H1: query indexes on hindcast_forecasts, model_states, forecasts, skill_scores
- H4: eval_period columns on skill_scores and skill_diagrams

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- C1: Unique constraints for deduplication ---
    op.create_index(
        "uq_observations_natural_key",
        "observations",
        ["station_id", "timestamp", "parameter", "source"],
        unique=True,
    )
    op.create_index(
        "uq_weather_forecasts_natural_key",
        "weather_forecasts",
        [
            "station_id",
            "nwp_source",
            "cycle_time",
            "valid_time",
            "parameter",
            "spatial_type",
            sa.text("COALESCE(band_id, -1)"),
            sa.text("COALESCE(member_id, -1)"),
        ],
        unique=True,
    )
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "lead_time_hours",
            "metric",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(forcing_type, '')"),
        ],
        unique=True,
    )

    # --- C2: parameter/units on forecast header tables ---
    op.add_column("forecasts", sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"))
    op.add_column("forecasts", sa.Column("units", sa.Text, nullable=False, server_default="m3/s"))
    op.alter_column("forecasts", "parameter", server_default=None)
    op.alter_column("forecasts", "units", server_default=None)

    op.add_column("hindcast_forecasts", sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"))
    op.add_column("hindcast_forecasts", sa.Column("units", sa.Text, nullable=False, server_default="m3/s"))
    op.alter_column("hindcast_forecasts", "parameter", server_default=None)
    op.alter_column("hindcast_forecasts", "units", server_default=None)

    # --- H1: Query indexes ---
    op.create_index(
        "ix_hindcast_forecasts_station_model_step",
        "hindcast_forecasts",
        ["station_id", "model_id", "hindcast_step"],
    )
    op.create_index(
        "ix_model_states_station_model_issue_desc",
        "model_states",
        ["station_id", "model_id", sa.text("issue_time DESC")],
    )
    op.create_index(
        "ix_forecasts_station_issued_desc",
        "forecasts",
        ["station_id", sa.text("issued_at DESC")],
    )
    op.create_index(
        "ix_forecasts_issued_station",
        "forecasts",
        [sa.text("issued_at DESC"), "station_id"],
    )
    op.create_index(
        "uq_forecasts_station_model_issued",
        "forecasts",
        ["station_id", "model_id", "issued_at"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
    )
    op.create_index(
        "ix_skill_scores_station_model_version",
        "skill_scores",
        ["station_id", "model_id", "computation_version", "metric", "lead_time_hours"],
    )

    # --- H4: Evaluation period on skill tables ---
    op.add_column("skill_scores", sa.Column("eval_period_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("'1970-01-01T00:00:00Z'")))
    op.add_column("skill_scores", sa.Column("eval_period_end", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("'1970-01-01T00:00:00Z'")))
    op.alter_column("skill_scores", "eval_period_start", server_default=None)
    op.alter_column("skill_scores", "eval_period_end", server_default=None)

    op.add_column("skill_diagrams", sa.Column("eval_period_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("'1970-01-01T00:00:00Z'")))
    op.add_column("skill_diagrams", sa.Column("eval_period_end", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("'1970-01-01T00:00:00Z'")))
    op.alter_column("skill_diagrams", "eval_period_start", server_default=None)
    op.alter_column("skill_diagrams", "eval_period_end", server_default=None)


def downgrade() -> None:
    # H4
    op.drop_column("skill_diagrams", "eval_period_end")
    op.drop_column("skill_diagrams", "eval_period_start")
    op.drop_column("skill_scores", "eval_period_end")
    op.drop_column("skill_scores", "eval_period_start")

    # H1
    op.drop_index("ix_skill_scores_station_model_version", table_name="skill_scores")
    op.drop_index("uq_forecasts_station_model_issued", table_name="forecasts")
    op.drop_index("ix_forecasts_issued_station", table_name="forecasts")
    op.drop_index("ix_forecasts_station_issued_desc", table_name="forecasts")
    op.drop_index("ix_model_states_station_model_issue_desc", table_name="model_states")
    op.drop_index("ix_hindcast_forecasts_station_model_step", table_name="hindcast_forecasts")

    # C2
    op.drop_column("hindcast_forecasts", "units")
    op.drop_column("hindcast_forecasts", "parameter")
    op.drop_column("forecasts", "units")
    op.drop_column("forecasts", "parameter")

    # C1
    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.drop_index("uq_weather_forecasts_natural_key", table_name="weather_forecasts")
    op.drop_index("uq_observations_natural_key", table_name="observations")
