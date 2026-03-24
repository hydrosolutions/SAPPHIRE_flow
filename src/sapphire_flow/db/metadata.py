# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, BYTEA, INTERVAL, JSONB, UUID

metadata = sa.MetaData()

# ──────────────────────────────────────────────
# REFERENCE DATA
# ──────────────────────────────────────────────

parameters = sa.Table(
    "parameters",
    metadata,
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

# ──────────────────────────────────────────────
# STATION DOMAIN
# ──────────────────────────────────────────────

basins = sa.Table(
    "basins",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "geometry",
        Geometry("MULTIPOLYGON", srid=4326),
        nullable=False,
    ),
    sa.Column("area_km2", sa.Float, nullable=True),
    sa.Column("attributes", JSONB, nullable=True),
    sa.Column("band_geometries", JSONB, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("network", sa.Text, nullable=False),
    sa.UniqueConstraint("network", "code", name="uq_basins_network_code"),
)

stations = sa.Table(
    "stations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("code", sa.Text, nullable=False),
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
        "basin_id", UUID(as_uuid=True), sa.ForeignKey("basins.id"), nullable=True
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
    sa.Column("network", sa.Text, nullable=False),
    sa.Column(
        "ownership",
        sa.Text,
        sa.CheckConstraint("ownership IN ('own', 'foreign')"),
        nullable=False,
        server_default="own",
    ),
    sa.Column("wigos_id", sa.Text, nullable=True),
    sa.UniqueConstraint("network", "code", name="uq_stations_network_code"),
)

station_thresholds = sa.Table(
    "station_thresholds",
    metadata,
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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

station_weather_sources = sa.Table(
    "station_weather_sources",
    metadata,
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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
    sa.Column(
        "status",
        sa.Text,
        sa.CheckConstraint("status IN ('active', 'inactive')"),
        nullable=False,
        server_default="active",
    ),
    sa.PrimaryKeyConstraint("station_id", "nwp_source"),
)

station_groups = sa.Table(
    "station_groups",
    metadata,
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

station_group_members = sa.Table(
    "station_group_members",
    metadata,
    sa.Column(
        "group_id",
        UUID(as_uuid=True),
        sa.ForeignKey("station_groups.id"),
        nullable=False,
    ),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("group_id", "station_id"),
)

sa.Index(
    "ix_station_group_members_station_id",
    station_group_members.c.station_id,
)

# ──────────────────────────────────────────────
# OBSERVATION DOMAIN
# v0: no rating_curve_id, no rating_curve_correction_version
# ──────────────────────────────────────────────

observations = sa.Table(
    "observations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("value", sa.Float, nullable=True),
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
            "qc_status IN ('raw', 'qc_passed', 'qc_failed', 'qc_suspect', 'missing')"
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
    sa.CheckConstraint(
        "(qc_status = 'missing') = (value IS NULL)",
        name="ck_observations_missing_value",
    ),
)

# Indexes on observations
sa.Index(
    "ix_observations_station_timestamp",
    observations.c.station_id,
    observations.c.timestamp,
)
sa.Index(
    "ix_observations_station_timestamp_qc_passed",
    observations.c.station_id,
    observations.c.timestamp,
    postgresql_where=observations.c.qc_status == "qc_passed",
)
sa.Index(
    "uq_observations_natural_key",
    observations.c.station_id,
    observations.c.timestamp,
    observations.c.parameter,
    observations.c.source,
    unique=True,
)

# ──────────────────────────────────────────────
# WEATHER / NWP DOMAIN
# v0: no is_gap, no gap_status
# ──────────────────────────────────────────────

weather_forecasts = sa.Table(
    "weather_forecasts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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

# Indexes on weather_forecasts
sa.Index(
    "ix_weather_forecasts_station_source_cycle_valid",
    weather_forecasts.c.station_id,
    weather_forecasts.c.nwp_source,
    weather_forecasts.c.cycle_time,
    weather_forecasts.c.valid_time,
)
sa.Index(
    "ix_weather_forecasts_station_source_valid_cycle_desc",
    weather_forecasts.c.station_id,
    weather_forecasts.c.nwp_source,
    weather_forecasts.c.valid_time,
    weather_forecasts.c.cycle_time.desc(),
)
sa.Index(
    "uq_weather_forecasts_natural_key",
    weather_forecasts.c.station_id,
    weather_forecasts.c.nwp_source,
    weather_forecasts.c.cycle_time,
    weather_forecasts.c.valid_time,
    weather_forecasts.c.parameter,
    weather_forecasts.c.spatial_type,
    sa.text("COALESCE(band_id, -1)"),
    sa.text("COALESCE(member_id, -1)"),
    unique=True,
)

# ──────────────────────────────────────────────
# HISTORICAL FORCING DOMAIN
# ──────────────────────────────────────────────

historical_forcing = sa.Table(
    "historical_forcing",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("version", sa.Text, nullable=False),
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
        name="ck_historical_forcing_band_id_consistency",
    ),
)

# Indexes on historical_forcing
sa.Index(
    "ix_historical_forcing_station_source_valid",
    historical_forcing.c.station_id,
    historical_forcing.c.source,
    historical_forcing.c.valid_time,
)
sa.Index(
    "uq_historical_forcing_natural_key",
    historical_forcing.c.station_id,
    historical_forcing.c.source,
    historical_forcing.c.version,
    historical_forcing.c.valid_time,
    historical_forcing.c.parameter,
    historical_forcing.c.spatial_type,
    sa.text("COALESCE(band_id, -1)"),
    sa.text("COALESCE(member_id, -1)"),
    unique=True,
)

# ──────────────────────────────────────────────
# MODEL DOMAIN
# ──────────────────────────────────────────────

models = sa.Table(
    "models",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column(
        "artifact_scope",
        sa.Text,
        sa.CheckConstraint("artifact_scope IN ('station', 'group')"),
        nullable=False,
    ),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

model_artifacts = sa.Table(
    "model_artifacts",
    metadata,
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

# Partial unique indexes: at most one active artifact per scope
sa.Index(
    "ix_model_artifacts_station_model_active",
    model_artifacts.c.station_id,
    model_artifacts.c.model_id,
    unique=True,
    postgresql_where=sa.and_(
        model_artifacts.c.status == "active",
        model_artifacts.c.station_id.isnot(None),
    ),
)
sa.Index(
    "ix_model_artifacts_group_model_active",
    model_artifacts.c.group_id,
    model_artifacts.c.model_id,
    unique=True,
    postgresql_where=sa.and_(
        model_artifacts.c.status == "active",
        model_artifacts.c.group_id.isnot(None),
    ),
)

# INVARIANT: model_assignments must only reference stations with ownership='own'.
# Foreign stations are display-only and never run through local models.
# Enforced at application layer; DB trigger deferred to v1.
model_assignments = sa.Table(
    "model_assignments",
    metadata,
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
    sa.Column("time_step", INTERVAL, nullable=False),
    sa.Column(
        "status",
        sa.Text,
        sa.CheckConstraint("status IN ('active', 'inactive')"),
        nullable=False,
        server_default="active",
    ),
    sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("station_id", "model_id"),
)

model_states = sa.Table(
    "model_states",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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

# Indexes on model_states
sa.Index(
    "ix_model_states_station_model_issue_desc",
    model_states.c.station_id,
    model_states.c.model_id,
    model_states.c.issue_time.desc(),
)

# ──────────────────────────────────────────────
# FORECAST DOMAIN
# ──────────────────────────────────────────────

forecasts = sa.Table(
    "forecasts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
    sa.Column(
        "model_artifact_id",
        UUID(as_uuid=True),
        sa.ForeignKey("model_artifacts.id"),
        nullable=False,
    ),
    sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("nwp_cycle_reference_time", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "nwp_cycle_source",
        sa.Text,
        sa.CheckConstraint("nwp_cycle_source IN ('primary', 'fallback')"),
        nullable=False,
        server_default="primary",
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
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("units", sa.Text, nullable=False),
)

forecast_values = sa.Table(
    "forecast_values",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "forecast_id", UUID(as_uuid=True), sa.ForeignKey("forecasts.id"), nullable=False
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

# Index on forecast_values
sa.Index(
    "ix_forecast_values_forecast_valid_time",
    forecast_values.c.forecast_id,
    forecast_values.c.valid_time,
)

# Indexes on forecasts
sa.Index(
    "ix_forecasts_station_issued_desc",
    forecasts.c.station_id,
    forecasts.c.issued_at.desc(),
)
sa.Index(
    "ix_forecasts_issued_station",
    forecasts.c.issued_at.desc(),
    forecasts.c.station_id,
)
sa.Index(
    "uq_forecasts_station_model_issued",
    forecasts.c.station_id,
    forecasts.c.model_id,
    forecasts.c.issued_at,
    unique=True,
    postgresql_where=forecasts.c.status != "superseded",
)

hindcast_forecasts = sa.Table(
    "hindcast_forecasts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("units", sa.Text, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

# Indexes on hindcast_forecasts
sa.Index(
    "ix_hindcast_forecasts_station_model_step",
    hindcast_forecasts.c.station_id,
    hindcast_forecasts.c.model_id,
    hindcast_forecasts.c.hindcast_step,
)

hindcast_values = sa.Table(
    "hindcast_values",
    metadata,
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

# ──────────────────────────────────────────────
# BASELINE DOMAIN
# ──────────────────────────────────────────────

clim_baselines = sa.Table(
    "clim_baselines",
    metadata,
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("day_of_year", sa.Integer, nullable=False),
    sa.Column("rolling_mean", sa.Float, nullable=False),
    sa.Column("rolling_std", sa.Float, nullable=False),
    sa.Column("sample_count", sa.Integer, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("station_id", "parameter", "day_of_year"),
    sa.CheckConstraint(
        "day_of_year >= 1 AND day_of_year <= 366",
        name="ck_clim_baselines_day_of_year",
    ),
)

# ──────────────────────────────────────────────
# SKILL DOMAIN
# ──────────────────────────────────────────────

flow_regime_configs = sa.Table(
    "flow_regime_configs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("p50", sa.Float, nullable=False),
    sa.Column("p90", sa.Float, nullable=False),
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

skill_scores = sa.Table(
    "skill_scores",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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
    sa.Column(
        "freshness",
        sa.Text,
        sa.CheckConstraint("freshness IN ('current', 'stale')"),
        nullable=False,
        server_default="current",
    ),
    sa.Column("eval_period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("eval_period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

skill_diagrams = sa.Table(
    "skill_diagrams",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
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
        sa.CheckConstraint("diagram_type IN ('reliability', 'roc', 'rank_histogram')"),
        nullable=False,
    ),
    sa.Column("threshold_level", sa.Text, nullable=True),
    sa.Column("data", JSONB, nullable=False),
    sa.Column("eval_period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("eval_period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

# Indexes on skill_scores
sa.Index(
    "uq_skill_scores_natural_key",
    skill_scores.c.station_id,
    skill_scores.c.model_artifact_id,
    skill_scores.c.skill_source,
    skill_scores.c.lead_time_hours,
    skill_scores.c.metric,
    sa.text("COALESCE(season, '')"),
    sa.text("COALESCE(flow_regime, '')"),
    sa.text("COALESCE(forcing_type, '')"),
    unique=True,
)
sa.Index(
    "ix_skill_scores_station_model_version",
    skill_scores.c.station_id,
    skill_scores.c.model_id,
    skill_scores.c.computation_version,
    skill_scores.c.metric,
    skill_scores.c.lead_time_hours,
)
sa.Index(
    "ix_skill_scores_station_freshness",
    skill_scores.c.station_id,
    skill_scores.c.freshness,
    skill_scores.c.eval_period_start,
    skill_scores.c.eval_period_end,
    postgresql_where=skill_scores.c.freshness == "current",
)

# ──────────────────────────────────────────────
# OPS DOMAIN
# ──────────────────────────────────────────────

alerts = sa.Table(
    "alerts",
    metadata,
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

# Deduplication partial unique indexes
sa.Index(
    "ix_alerts_station_level_source_active",
    alerts.c.station_id,
    alerts.c.alert_level,
    alerts.c.source,
    unique=True,
    postgresql_where=sa.and_(
        alerts.c.status.in_(["raised", "acknowledged"]),
        alerts.c.station_id.isnot(None),
    ),
)
sa.Index(
    "ix_alerts_level_source_system_active",
    alerts.c.alert_level,
    alerts.c.source,
    unique=True,
    postgresql_where=sa.and_(
        alerts.c.status.in_(["raised", "acknowledged"]),
        alerts.c.station_id.is_(None),
    ),
)

pipeline_health = sa.Table(
    "pipeline_health",
    metadata,
    sa.Column(
        "id",
        BIGINT,
        primary_key=True,
        autoincrement=True,
    ),
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
