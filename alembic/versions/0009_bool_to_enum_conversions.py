"""Convert boolean columns to text enum columns

Converts nwp_cycle_is_fallback, active, is_active, and is_stale
from boolean to text enum columns per enums-over-booleans rule.

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- forecasts: nwp_cycle_is_fallback (bool) → nwp_cycle_source (text) ---
    op.add_column("forecasts", sa.Column("nwp_cycle_source", sa.Text, nullable=True))
    op.execute(
        "UPDATE forecasts SET nwp_cycle_source = "
        "CASE WHEN nwp_cycle_is_fallback THEN 'fallback' ELSE 'primary' END"
    )
    op.alter_column(
        "forecasts", "nwp_cycle_source", nullable=False, server_default="primary"
    )
    op.drop_column("forecasts", "nwp_cycle_is_fallback")
    op.create_check_constraint(
        "ck_forecasts_nwp_cycle_source",
        "forecasts",
        "nwp_cycle_source IN ('primary', 'fallback')",
    )

    # --- station_weather_sources: active (bool) → status (text) ---
    op.add_column(
        "station_weather_sources", sa.Column("status", sa.Text, nullable=True)
    )
    op.execute(
        "UPDATE station_weather_sources SET status = "
        "CASE WHEN active THEN 'active' ELSE 'inactive' END"
    )
    op.alter_column(
        "station_weather_sources", "status", nullable=False, server_default="active"
    )
    op.drop_column("station_weather_sources", "active")
    op.create_check_constraint(
        "ck_station_weather_sources_status",
        "station_weather_sources",
        "status IN ('active', 'inactive')",
    )

    # --- model_assignments: is_active (bool) → status (text) ---
    op.add_column("model_assignments", sa.Column("status", sa.Text, nullable=True))
    op.execute(
        "UPDATE model_assignments SET status = "
        "CASE WHEN is_active THEN 'active' ELSE 'inactive' END"
    )
    op.alter_column(
        "model_assignments", "status", nullable=False, server_default="active"
    )
    op.drop_column("model_assignments", "is_active")
    op.create_check_constraint(
        "ck_model_assignments_status",
        "model_assignments",
        "status IN ('active', 'inactive')",
    )

    # --- skill_scores: is_stale (bool) → freshness (text) ---
    op.add_column("skill_scores", sa.Column("freshness", sa.Text, nullable=True))
    op.execute(
        "UPDATE skill_scores SET freshness = "
        "CASE WHEN is_stale THEN 'stale' ELSE 'current' END"
    )
    op.alter_column(
        "skill_scores", "freshness", nullable=False, server_default="current"
    )
    op.drop_column("skill_scores", "is_stale")
    op.create_check_constraint(
        "ck_skill_scores_freshness",
        "skill_scores",
        "freshness IN ('current', 'stale')",
    )


def downgrade() -> None:
    # --- skill_scores: freshness → is_stale ---
    op.add_column("skill_scores", sa.Column("is_stale", sa.Boolean, nullable=True))
    op.execute("UPDATE skill_scores SET is_stale = (freshness = 'stale')")
    op.alter_column(
        "skill_scores", "is_stale", nullable=False, server_default="false"
    )
    op.drop_constraint("ck_skill_scores_freshness", "skill_scores")
    op.drop_column("skill_scores", "freshness")

    # --- model_assignments: status → is_active ---
    op.add_column(
        "model_assignments", sa.Column("is_active", sa.Boolean, nullable=True)
    )
    op.execute("UPDATE model_assignments SET is_active = (status = 'active')")
    op.alter_column(
        "model_assignments", "is_active", nullable=False, server_default="true"
    )
    op.drop_constraint("ck_model_assignments_status", "model_assignments")
    op.drop_column("model_assignments", "status")

    # --- station_weather_sources: status → active ---
    op.add_column(
        "station_weather_sources", sa.Column("active", sa.Boolean, nullable=True)
    )
    op.execute("UPDATE station_weather_sources SET active = (status = 'active')")
    op.alter_column(
        "station_weather_sources", "active", nullable=False, server_default="true"
    )
    op.drop_constraint("ck_station_weather_sources_status", "station_weather_sources")
    op.drop_column("station_weather_sources", "status")

    # --- forecasts: nwp_cycle_source → nwp_cycle_is_fallback ---
    op.add_column(
        "forecasts", sa.Column("nwp_cycle_is_fallback", sa.Boolean, nullable=True)
    )
    op.execute(
        "UPDATE forecasts SET nwp_cycle_is_fallback = (nwp_cycle_source = 'fallback')"
    )
    op.alter_column(
        "forecasts", "nwp_cycle_is_fallback", nullable=False, server_default="false"
    )
    op.drop_constraint("ck_forecasts_nwp_cycle_source", "forecasts")
    op.drop_column("forecasts", "nwp_cycle_source")
