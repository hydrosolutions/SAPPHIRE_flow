"""v0 schema

Revision ID: 0001
Revises:
Create Date: 2026-03-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, BYTEA, INTERVAL, JSONB, UUID

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── REFERENCE DATA ─────────────────────────────────────────────────────────

    op.create_table(
        "parameters",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column(
            "parameter_domain",
            sa.Text,
            sa.CheckConstraint("parameter_domain IN ('river', 'weather')"),
            nullable=False,
        ),
        sa.Column(
            "aggregation_method",
            sa.Text,
            sa.CheckConstraint("aggregation_method IN ('sum', 'mean')"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── STATION DOMAIN ─────────────────────────────────────────────────────────

    op.create_table(
        "basins",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("geometry", Geometry("MULTIPOLYGON", srid=4326), nullable=False),
        sa.Column("area_km2", sa.Float, nullable=True),
        sa.Column("attributes", JSONB, nullable=True),
        sa.Column("band_geometries", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_basins_geometry", "basins", ["geometry"], postgresql_using="gist"
    )

    op.create_table(
        "stations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("location", Geometry("POINT", srid=4326), nullable=False),
        sa.Column("altitude_masl", sa.Float, nullable=True),
        sa.Column(
            "station_kind",
            sa.Text,
            sa.CheckConstraint("station_kind IN ('weather', 'river')"),
            nullable=False,
        ),
        sa.Column(
            "basin_id",
            UUID(as_uuid=True),
            sa.ForeignKey("basins.id"),
            nullable=True,
        ),
        sa.Column("timezone", sa.Text, nullable=False),
        sa.Column("regulation_type", sa.Text, nullable=True),
        sa.Column("forecast_target", sa.Text, nullable=True),
        sa.Column("measured_parameters", ARRAY(sa.Text), nullable=False),
        sa.Column(
            "station_status",
            sa.Text,
            sa.CheckConstraint(
                "station_status IN "
                "('onboarding', 'operational', 'suspended', 'decommissioned')"
            ),
            nullable=False,
            server_default="onboarding",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_stations_location", "stations", ["location"], postgresql_using="gist"
    )
    op.create_index("ix_stations_station_kind", "stations", ["station_kind"])
    op.create_index("ix_stations_station_status", "stations", ["station_status"])

    op.create_table(
        "station_thresholds",
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("danger_level", sa.Text, nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column(
            "source",
            sa.Text,
            sa.CheckConstraint("source IN ('authority', 'inferred')"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("station_id", "danger_level", "parameter"),
    )

    op.create_table(
        "station_weather_sources",
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("nwp_source", sa.Text, nullable=False),
        sa.Column(
            "extraction_type",
            sa.Text,
            sa.CheckConstraint(
                "extraction_type IN ('point', 'basin_average', 'elevation_band')"
            ),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("station_id", "nwp_source"),
    )

    op.create_table(
        "station_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "station_group_members",
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("station_groups.id"),
            nullable=False,
        ),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("group_id", "station_id"),
    )

    # ── OBSERVATION DOMAIN ─────────────────────────────────────────────────────
    # v0: no rating_curve_id, no rating_curve_correction_version

    op.create_table(
        "observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column(
            "source",
            sa.Text,
            sa.CheckConstraint("source IN ('measured', 'manual_import')"),
            nullable=False,
        ),
        sa.Column(
            "qc_status",
            sa.Text,
            sa.CheckConstraint(
                "qc_status IN ('raw', 'qc_passed', 'qc_failed', 'qc_suspect')"
            ),
            nullable=False,
            server_default="raw",
        ),
        sa.Column("qc_flags", JSONB, nullable=True),
        sa.Column("qc_rule_version", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_observations_station_timestamp",
        "observations",
        ["station_id", "timestamp"],
    )
    op.create_index(
        "ix_observations_station_timestamp_qc_passed",
        "observations",
        ["station_id", "timestamp"],
        postgresql_where=sa.text("qc_status = 'qc_passed'"),
    )

    # ── WEATHER / NWP DOMAIN ───────────────────────────────────────────────────
    # v0: no is_gap, no gap_status

    op.create_table(
        "weather_forecasts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("nwp_source", sa.Text, nullable=False),
        sa.Column("cycle_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column(
            "spatial_type",
            sa.Text,
            sa.CheckConstraint(
                "spatial_type IN ('point', 'basin_average', 'elevation_band')"
            ),
            nullable=False,
        ),
        sa.Column("band_id", sa.Integer, nullable=True),
        sa.Column("member_id", sa.Integer, nullable=True),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(spatial_type = 'elevation_band' AND band_id IS NOT NULL) OR "
            "(spatial_type != 'elevation_band' AND band_id IS NULL)",
            name="ck_weather_forecasts_band_id_consistency",
        ),
    )
    op.create_index(
        "ix_weather_forecasts_station_source_cycle_valid",
        "weather_forecasts",
        ["station_id", "nwp_source", "cycle_time", "valid_time"],
    )
    op.create_index(
        "ix_weather_forecasts_station_source_valid_cycle",
        "weather_forecasts",
        ["station_id", "nwp_source", "valid_time", "cycle_time"],
    )

    # ── MODEL DOMAIN ───────────────────────────────────────────────────────────

    op.create_table(
        "models",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column(
            "artifact_scope",
            sa.Text,
            sa.CheckConstraint("artifact_scope IN ('station', 'group')"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "model_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=True,
        ),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("station_groups.id"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint(
                "status IN "
                "('training', 'pending_approval', 'active', 'superseded', 'rejected')"
            ),
            nullable=False,
        ),
        sa.Column("artifact_path", sa.Text, nullable=False),
        sa.Column("training_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("training_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_by", UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(station_id IS NOT NULL) != (group_id IS NOT NULL)",
            name="ck_model_artifacts_scope_xor",
        ),
    )
    op.create_index(
        "ix_model_artifacts_station_model_active",
        "model_artifacts",
        ["station_id", "model_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND station_id IS NOT NULL"),
    )
    op.create_index(
        "ix_model_artifacts_group_model_active",
        "model_artifacts",
        ["group_id", "model_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND group_id IS NOT NULL"),
    )

    op.create_table(
        "model_assignments",
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column("time_step", INTERVAL, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("station_id", "model_id"),
    )

    op.create_table(
        "model_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column("issue_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_bytes", BYTEA, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── FORECAST DOMAIN ────────────────────────────────────────────────────────

    op.create_table(
        "forecasts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "model_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            nullable=False,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "nwp_cycle_reference_time", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "nwp_cycle_is_fallback",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "representation",
            sa.Text,
            sa.CheckConstraint("representation IN ('members', 'quantiles')"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint("status IN ('raw', 'reviewed', 'published')"),
            nullable=False,
            server_default="raw",
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("warm_up_source", sa.Text, nullable=True),
        sa.Column("warm_up_state_age_hours", sa.Float, nullable=True),
        sa.Column("observation_staleness_hours", sa.Float, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "forecast_values",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "forecast_id",
            UUID(as_uuid=True),
            sa.ForeignKey("forecasts.id"),
            nullable=False,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_time_hours", sa.Integer, nullable=False),
        sa.Column("member_id", sa.Integer, nullable=True),
        sa.Column("quantile", sa.Float, nullable=True),
        sa.Column("value", sa.Float, nullable=False),
        sa.CheckConstraint(
            "(member_id IS NOT NULL) != (quantile IS NOT NULL)",
            name="ck_forecast_values_representation_xor",
        ),
    )
    op.create_index(
        "ix_forecast_values_forecast_valid_time",
        "forecast_values",
        ["forecast_id", "valid_time"],
    )

    op.create_table(
        "hindcast_forecasts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "model_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            nullable=False,
        ),
        sa.Column("hindcast_step", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "forcing_type",
            sa.Text,
            sa.CheckConstraint("forcing_type IN ('nwp_archive', 'reanalysis')"),
            nullable=False,
        ),
        sa.Column(
            "representation",
            sa.Text,
            sa.CheckConstraint("representation IN ('members', 'quantiles')"),
            nullable=False,
        ),
        sa.Column("hindcast_run_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "hindcast_values",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hindcast_forecast_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hindcast_forecasts.id"),
            nullable=False,
        ),
        sa.Column("hindcast_step", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_time_hours", sa.Integer, nullable=False),
        sa.Column("member_id", sa.Integer, nullable=True),
        sa.Column("quantile", sa.Float, nullable=True),
        sa.Column("value", sa.Float, nullable=False),
        sa.CheckConstraint(
            "(member_id IS NOT NULL) != (quantile IS NOT NULL)",
            name="ck_hindcast_values_representation_xor",
        ),
    )

    # ── SKILL DOMAIN ───────────────────────────────────────────────────────────

    op.create_table(
        "flow_regime_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("q50", sa.Float, nullable=False),
        sa.Column("q90", sa.Float, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_count", sa.Integer, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "skill_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "model_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            nullable=False,
        ),
        sa.Column("skill_source", sa.Text, nullable=False),
        sa.Column("forcing_type", sa.Text, nullable=True),
        sa.Column("computation_version", sa.Integer, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_time_hours", sa.Integer, nullable=False),
        sa.Column("season", sa.Text, nullable=True),
        sa.Column("flow_regime", sa.Text, nullable=True),
        sa.Column(
            "flow_regime_config_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flow_regime_configs.id"),
            nullable=True,
        ),
        sa.Column("metric", sa.Text, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("is_stale", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "skill_diagrams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "model_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            nullable=False,
        ),
        sa.Column("skill_source", sa.Text, nullable=False),
        sa.Column("computation_version", sa.Integer, nullable=False),
        sa.Column("lead_time_hours", sa.Integer, nullable=False),
        sa.Column("season", sa.Text, nullable=True),
        sa.Column("flow_regime", sa.Text, nullable=True),
        sa.Column(
            "flow_regime_config_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flow_regime_configs.id"),
            nullable=True,
        ),
        sa.Column(
            "diagram_type",
            sa.Text,
            sa.CheckConstraint(
                "diagram_type IN ('reliability', 'roc', 'rank_histogram')"
            ),
            nullable=False,
        ),
        sa.Column("threshold_level", sa.Text, nullable=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── OPS DOMAIN ─────────────────────────────────────────────────────────────

    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=True,
        ),
        sa.Column(
            "source",
            sa.Text,
            sa.CheckConstraint("source IN ('forecast', 'observation', 'pipeline')"),
            nullable=False,
        ),
        sa.Column("alert_level", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint("status IN ('raised', 'acknowledged', 'resolved')"),
            nullable=False,
            server_default="raised",
        ),
        sa.Column("trigger_probability", sa.Float, nullable=True),
        sa.Column("trigger_value", sa.Float, nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_alerts_station_level_source_active",
        "alerts",
        ["station_id", "alert_level", "source"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('raised', 'acknowledged') AND station_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_alerts_level_source_system_active",
        "alerts",
        ["alert_level", "source"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('raised', 'acknowledged') AND station_id IS NULL"
        ),
    )

    op.create_table(
        "pipeline_health",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column("check_type", sa.Text, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint("status IN ('ok', 'warning', 'critical')"),
            nullable=False,
        ),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("cycle_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── SEED: canonical parameters ─────────────────────────────────────────────

    parameters_table = sa.table(
        "parameters",
        sa.column("name", sa.Text),
        sa.column("display_name", sa.Text),
        sa.column("unit", sa.Text),
        sa.column("parameter_domain", sa.Text),
        sa.column("aggregation_method", sa.Text),
    )
    op.bulk_insert(
        parameters_table,
        [
            {
                "name": "discharge",
                "display_name": "Discharge",
                "unit": "m³/s",
                "parameter_domain": "river",
                "aggregation_method": "mean",
            },
            {
                "name": "water_level",
                "display_name": "Water Level",
                "unit": "m",
                "parameter_domain": "river",
                "aggregation_method": "mean",
            },
            {
                "name": "precipitation",
                "display_name": "Precipitation",
                "unit": "mm",
                "parameter_domain": "weather",
                "aggregation_method": "sum",
            },
            {
                "name": "temperature",
                "display_name": "Temperature",
                "unit": "°C",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
            {
                "name": "humidity",
                "display_name": "Relative Humidity",
                "unit": "%",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
            {
                "name": "radiation",
                "display_name": "Solar Radiation",
                "unit": "W/m²",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
            {
                "name": "wind_speed",
                "display_name": "Wind Speed",
                "unit": "m/s",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
            {
                "name": "snow_depth",
                "display_name": "Snow Depth",
                "unit": "cm",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
            {
                "name": "reference_et",
                "display_name": "Reference Evapotranspiration",
                "unit": "mm/h",
                "parameter_domain": "weather",
                "aggregation_method": "sum",
            },
            {
                "name": "swe",
                "display_name": "Snow Water Equivalent",
                "unit": "mm",
                "parameter_domain": "weather",
                "aggregation_method": "mean",
            },
        ],
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("pipeline_health")
    op.drop_index("ix_alerts_level_source_system_active", table_name="alerts")
    op.drop_index("ix_alerts_station_level_source_active", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("skill_diagrams")
    op.drop_table("skill_scores")
    op.drop_table("flow_regime_configs")
    op.drop_table("hindcast_values")
    op.drop_table("hindcast_forecasts")
    op.drop_index(
        "ix_forecast_values_forecast_valid_time", table_name="forecast_values"
    )
    op.drop_table("forecast_values")
    op.drop_table("forecasts")
    op.drop_table("model_states")
    op.drop_table("model_assignments")
    op.drop_index("ix_model_artifacts_group_model_active", table_name="model_artifacts")
    op.drop_index(
        "ix_model_artifacts_station_model_active", table_name="model_artifacts"
    )
    op.drop_table("model_artifacts")
    op.drop_table("models")
    op.drop_index(
        "ix_weather_forecasts_station_source_valid_cycle",
        table_name="weather_forecasts",
    )
    op.drop_index(
        "ix_weather_forecasts_station_source_cycle_valid",
        table_name="weather_forecasts",
    )
    op.drop_table("weather_forecasts")
    op.drop_index(
        "ix_observations_station_timestamp_qc_passed", table_name="observations"
    )
    op.drop_index("ix_observations_station_timestamp", table_name="observations")
    op.drop_table("observations")
    op.drop_table("station_group_members")
    op.drop_table("station_groups")
    op.drop_table("station_weather_sources")
    op.drop_table("station_thresholds")
    op.drop_index("ix_stations_station_status", table_name="stations")
    op.drop_index("ix_stations_station_kind", table_name="stations")
    op.drop_index("ix_stations_location", table_name="stations")
    op.drop_table("stations")
    op.drop_index("ix_basins_geometry", table_name="basins")
    op.drop_table("basins")
    op.drop_table("parameters")
