# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import (
    ARRAY,
    BIGINT,
    BYTEA,
    INET,
    INTERVAL,
    JSONB,
    UUID,
)

metadata = sa.MetaData()

# Plan 147 Slice A: every `tenant_id` column is NOT NULL with NO server
# default — tenant ownership is an explicit decision at every writer boundary
# (migrations 0042-0044 backfill the seeded `sapphire` tenant onto pre-existing
# rows, then DROP the backfill default). A raw INSERT that omits tenant_id
# therefore fails loud (NotNullViolation) instead of silently defaulting to
# Swiss.

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

# Plan 120 Task 0A: provenance for an accepted basin/static package
# (docs/requirements/04-basin-static-artifact-contract.md). `package_id` is
# the PRODUCER-declared identifier (manifest.json "package_id"), not a
# SAP3-generated UUID — see tests/fixtures/basin_static/nepal-dhm-basins/
# manifest.json:3. Package files themselves are discarded after import;
# `checksums` retains the computed payload-set hashes (`04:429-430`).
basin_static_packages = sa.Table(
    "basin_static_packages",
    metadata,
    sa.Column("package_id", sa.Text, primary_key=True),
    sa.Column("network", sa.Text, nullable=False),
    sa.Column("contract_version", sa.Text, nullable=False),
    sa.Column("checksums", JSONB, nullable=False),
    sa.Column("extractor_name", sa.Text, nullable=True),
    sa.Column("extractor_version", sa.Text, nullable=True),
    sa.Column("source_datasets", JSONB, nullable=True),
    sa.Column("climatology_window", JSONB, nullable=True),
    sa.Column(
        "imported_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # Plan 120 Phase 2 fixer round (2026-07-23): a deterministic canonical
    # fingerprint of the validated manifest metadata + computed payload
    # checksums (`types/basin_package.py::compute_package_fingerprint`).
    # Re-imports compare against this stored value so a manifest-only mutation
    # under the same `package_id` is caught as an immutability violation, not a
    # silent no-op. Additive; nullable so it is additive over 0039's table.
    sa.Column("fingerprint", sa.Text, nullable=True),
)

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
    sa.Column("regional_basin", sa.Text, nullable=True),
    sa.Column("band_geometries", JSONB, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("network", sa.Text, nullable=False),
    # Plan 120 Task 0A: the basin/static package that produced the CURRENT
    # projection row (additive, nullable — NULL for legacy/non-package
    # basins).
    sa.Column(
        "package_id",
        sa.Text,
        sa.ForeignKey("basin_static_packages.package_id"),
        nullable=True,
    ),
    sa.UniqueConstraint("network", "code", name="uq_basins_network_code"),
)

# Plan 120 Task 0A: append-only version history for `basins`, keyed to the
# STABLE `basins.id` (inbound FKs from `stations.basin_id` and the §5a table
# stay valid across corrections — see "Versioned basin state" in
# docs/plans/120-basin-static-importer.md). `basins` keeps projecting the
# CURRENT version (readers unchanged); this table is the audit trail +
# lineage-join target. `package_id` NULLable — legacy/non-package rows carry
# NULL (Task 0A "Legacy backfill" / "Ongoing non-package basin inserts").
# `gateway_mapping` is a snapshot of this version's §5a rows, sourced from
# the in-memory Task 1B package structure (never a DB read-back — see
# "gateway_mapping source of truth").
basin_versions = sa.Table(
    "basin_versions",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "basin_id", UUID(as_uuid=True), sa.ForeignKey("basins.id"), nullable=False
    ),
    sa.Column(
        "package_id",
        sa.Text,
        sa.ForeignKey("basin_static_packages.package_id"),
        nullable=True,
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("geometry", Geometry("MULTIPOLYGON", srid=4326), nullable=False),
    sa.Column("attributes", JSONB, nullable=True),
    sa.Column("area_km2", sa.Float, nullable=True),
    sa.Column("band_geometries", JSONB, nullable=True),
    sa.Column("gateway_mapping", JSONB, nullable=True),
    # NULL = current version. Exactly one current row per basin is enforced
    # by the partial unique index below, not by this column alone.
    sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        # clock_timestamp() precedent (historical_forcing:642) — a row-level
        # wall clock so a correction transaction's stamp-then-append pair
        # gets distinct, insertion-ordered created_at values.
        server_default=sa.func.clock_timestamp(),
    ),
    sa.UniqueConstraint("basin_id", "version", name="uq_basin_versions_basin_version"),
)

# Exactly one current (superseded_at IS NULL) version per basin — a DB-
# enforced invariant the correction transaction must respect by stamping the
# prior current row's superseded_at BEFORE inserting the new current row
# (major review finding — see plan "Versioned basin state").
sa.Index(
    "uq_basin_versions_one_current_per_basin",
    basin_versions.c.basin_id,
    unique=True,
    postgresql_where=basin_versions.c.superseded_at.is_(None),
)

# Plan 120 Task 0A: lineage join table — which basin VERSION(S) a model
# artifact actually trained on. A join table (not a singular FK on
# `model_artifacts`) because a GROUP-scoped artifact spans many stations →
# many basins → many basin_versions (see plan "Versioned basin state").
# `model_artifacts` itself gains no new column.
model_artifact_basin_versions = sa.Table(
    "model_artifact_basin_versions",
    metadata,
    sa.Column(
        "model_artifact_id",
        UUID(as_uuid=True),
        sa.ForeignKey("model_artifacts.id"),
        nullable=False,
    ),
    sa.Column(
        "basin_version_id",
        UUID(as_uuid=True),
        sa.ForeignKey("basin_versions.id"),
        nullable=False,
    ),
    sa.PrimaryKeyConstraint("model_artifact_id", "basin_version_id"),
)

# The correction path looks up lineage rows by `basin_version_id`, which the
# composite PK `(model_artifact_id, basin_version_id)` cannot serve (its leading
# column is `model_artifact_id`). A dedicated single-column index makes that
# query index-servable.
sa.Index(
    "ix_model_artifact_basin_versions_basin_version_id",
    model_artifact_basin_versions.c.basin_version_id,
)

# Plan 147 Slice A: the tenant-model foundation. `code` is the human/config
# handle (e.g. "sapphire", "dhm") resolved to a TenantId once at the
# config/CLI boundary (services/tenant_boundary.py). Seeded with a default
# `sapphire` tenant by migration 0041 (types/tenant.py:DEFAULT_TENANT_ID).
tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("code", sa.Text, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
)

stations = sa.Table(
    "stations",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("location", Geometry("POINT", srid=4326), nullable=False),
    sa.Column("altitude_masl", sa.Float, nullable=True),
    sa.Column("water_level_datum_masl", sa.Float, nullable=True),
    sa.Column("water_level_unit", sa.Text, nullable=True),
    sa.Column(
        "station_kind",
        sa.Text,
        sa.CheckConstraint("station_kind IN ('weather', 'river', 'lake')"),
        nullable=False,
    ),
    sa.Column(
        "basin_id", UUID(as_uuid=True), sa.ForeignKey("basins.id"), nullable=True
    ),
    sa.Column("timezone", sa.Text, nullable=False),
    sa.Column("regulation_type", sa.Text, nullable=True),
    sa.Column("forecast_targets", JSONB, nullable=True),
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
    sa.Column(
        "gauging_status",
        sa.Text,
        sa.CheckConstraint(
            "gauging_status IN ('gauged', 'ungauged', 'calculated')",
            name="ck_stations_gauging_status",
        ),
        nullable=False,
        server_default="gauged",
    ),
    # Plan 147 Slice A: canonical tenant ownership (R4 LOCKED). Added by
    # migration 0042 (add-nullable -> backfill `sapphire` -> NOT NULL).
    sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id"),
        nullable=False,
    ),
    sa.UniqueConstraint("network", "code", name="uq_stations_network_code"),
    # (id, tenant_id) is redundant with the PK alone but is the FK target the
    # station_group_members composite FK binds tenant identity through.
    sa.UniqueConstraint("id", "tenant_id", name="uq_stations_id_tenant_id"),
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
    sa.Column(
        "role",
        sa.Text,
        sa.CheckConstraint("role IS NULL OR role IN ('forecast', 'reanalysis')"),
        nullable=True,
    ),
    sa.PrimaryKeyConstraint("station_id", "nwp_source"),
)

# §5a mapping table (docs/requirements/04-basin-static-artifact-contract.md
# §5a; Plan 082 Task 2D). Additive — does not touch `basins`. Schema + reader
# owned by 082; rows populated by Plan 120's basin/static package importer.
recap_gateway_polygon_bindings = sa.Table(
    "recap_gateway_polygon_bindings",
    metadata,
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column(
        "basin_id", UUID(as_uuid=True), sa.ForeignKey("basins.id"), nullable=False
    ),
    sa.Column("gateway_hru_name", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "spatial_type",
        sa.Text,
        sa.CheckConstraint("spatial_type IN ('basin_average', 'elevation_band')"),
        nullable=False,
    ),
    sa.Column("band_id", sa.Integer, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # Plan 120 Task 0A: additive provenance columns (owner: 120). Schema +
    # the base six columns + the resolver stay owned by 082. Nullable so
    # 082's own fixture callers that omit them still compile/insert.
    sa.Column(
        "package_id",
        sa.Text,
        sa.ForeignKey("basin_static_packages.package_id"),
        nullable=True,
    ),
    sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("station_id", "gateway_hru_name", "name"),
)

# At most one basin_average binding per station (Codex review Finding 3):
# `GatewayPolygonResolver.resolve` picks `basin_average[0]` from
# `fetch_bindings_for_station` — the PK alone (station_id, gateway_hru_name,
# name) permits multiple basin_average rows per station (e.g. a lingering
# `g_5501_old` alongside `g_5501`), which would make resolution silently
# arbitrary/stale. Invalid states unrepresentable: Plan 120's §5a importer
# must upsert-REPLACE the basin_average binding for a station (delete-then-
# insert or an explicit replace), never accumulate additional rows.
sa.Index(
    "uq_recap_gateway_polygon_bindings_one_basin_average_per_station",
    recap_gateway_polygon_bindings.c.station_id,
    unique=True,
    postgresql_where=recap_gateway_polygon_bindings.c.spatial_type == "basin_average",
)

station_groups = sa.Table(
    "station_groups",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    # Plan 147 Slice A: name is unique PER TENANT (migration 0043 replaces the
    # old global UNIQUE(name) with UNIQUE(tenant_id, name)) — name alone is no
    # longer a key.
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id"),
        nullable=False,
    ),
    sa.UniqueConstraint("tenant_id", "name", name="uq_station_groups_tenant_id_name"),
    sa.UniqueConstraint("id", "tenant_id", name="uq_station_groups_id_tenant_id"),
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
    # Plan 147 Slice A: participates in TWO composite FKs below so tenant
    # identity is structurally forced to agree across station/group/member —
    # a mismatched row is unrepresentable at the DB, through every writer
    # (added by migration 0044).
    sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        nullable=False,
    ),
    sa.PrimaryKeyConstraint("group_id", "station_id"),
    sa.ForeignKeyConstraint(
        ["station_id", "tenant_id"],
        ["stations.id", "stations.tenant_id"],
        name="fk_station_group_members_station_tenant",
    ),
    sa.ForeignKeyConstraint(
        ["group_id", "tenant_id"],
        ["station_groups.id", "station_groups.tenant_id"],
        name="fk_station_group_members_group_tenant",
    ),
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
        # Plan 035 Task 2: 'rating_curve_derived' (DHM level->discharge) and
        # 'component_derived' (Plan 015, forward-compat) join the v0 set.
        sa.CheckConstraint(
            "source IN ('measured', 'manual_import', 'rating_curve_derived', "
            "'component_derived')",
            name="ck_observations_source",
        ),
        nullable=False,
    ),
    # Plan 035 Task 2: provenance for rating-curve-derived discharge. NULL for
    # directly-measured values. The composite FK below (station_id,
    # rating_curve_id) enforces that a row can only bind a curve for its OWN
    # station — MATCH SIMPLE skips the check when rating_curve_id IS NULL.
    sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=True),
    sa.Column("rating_curve_correction_version", sa.Text, nullable=True),
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
    # Plan 035 Task 2: a rating-curve-derived observation may only reference a
    # curve belonging to its own station (MATCH SIMPLE → skipped when NULL).
    sa.ForeignKeyConstraint(
        ["station_id", "rating_curve_id"],
        ["rating_curves.station_id", "rating_curves.id"],
        name="fk_observations_rating_curve_station",
    ),
    # Plan 035 Task 3: composite target so observation_versions can FK
    # (observation_id, station_id) and trust the denormalised station.
    sa.UniqueConstraint("id", "station_id", name="uq_observations_id_station"),
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
# Plan 035 Task 2 — Flow 12 Branch A provenance queries (by station + source).
sa.Index(
    "ix_observations_station_source_ts",
    observations.c.station_id,
    observations.c.source,
    observations.c.timestamp,
)

# ──────────────────────────────────────────────
# RATING CURVE DOMAIN
# Plan 035 Task 1. uploaded_by has no FK yet — users table does not exist in
# the migration chain; a later v1 migration adds the FK once it does.
# ──────────────────────────────────────────────

rating_curves = sa.Table(
    "rating_curves",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
    sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
    sa.Column("points", JSONB, nullable=False),
    sa.Column(
        "interpolation",
        sa.Text,
        nullable=False,
        server_default="linear",
    ),
    sa.Column("uploaded_by", UUID(as_uuid=True), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.UniqueConstraint(
        "station_id", "version", name="uq_rating_curves_station_version"
    ),
    # Plan 035 Task 2: target for the composite same-station FK on observations
    # and forecasts (id is already the PK; this makes (id, station_id) a valid
    # FK reference so a row cannot bind another station's curve).
    sa.UniqueConstraint("id", "station_id", name="uq_rating_curves_id_station"),
)

# Indexes on rating_curves
sa.Index(
    "ix_rating_curves_station_valid_from",
    rating_curves.c.station_id,
    rating_curves.c.valid_from.desc(),
)
sa.Index(
    "uq_rating_curves_station_active",
    rating_curves.c.station_id,
    unique=True,
    postgresql_where=rating_curves.c.valid_to.is_(None),
)

# Plan 035 Task 3: archive of discharge values superseded by a rating-curve
# reprocessing (Flow 12 Branch A). Lightweight — only value + curve refs change.
observation_versions = sa.Table(
    "observation_versions",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("observation_id", UUID(as_uuid=True), nullable=False),
    sa.Column("station_id", UUID(as_uuid=True), nullable=False),
    sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("value", sa.Float, nullable=True),  # NULL if superseded obs was MISSING
    sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "superseded_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("superseded_by_curve_id", UUID(as_uuid=True), nullable=False),
    # One archive row per (observation, producing-curve) — idempotent re-runs.
    sa.UniqueConstraint(
        "observation_id", "rating_curve_id", name="uq_observation_versions_obs_curve"
    ),
    # Station denormalisation is DB-trusted: the archived row's station must match
    # the referenced observation's station.
    sa.ForeignKeyConstraint(
        ["observation_id", "station_id"],
        ["observations.id", "observations.station_id"],
        name="fk_observation_versions_observation_station",
    ),
    # Both the producing and the superseding curve must belong to this station.
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

# Index on observation_versions — epoch-matched lookups (Plan 035 §4).
sa.Index(
    "ix_observation_versions_station_param_ts_curve",
    observation_versions.c.station_id,
    observation_versions.c.parameter,
    observation_versions.c.timestamp,
    observation_versions.c.rating_curve_id,
)

# Plan 015: calculated station formulas — Q_virtual = Σ(wᵢ · Qᵢ) over gauged
# components. One row per (calculated station, component, parameter) validity
# window; the eligibility trigger (added in a separate migration) enforces
# target=CALCULATED + component GAUGED+operational on insert / relation-changing
# update (closure-only updates exempt).
calculated_station_formulas = sa.Table(
    "calculated_station_formulas",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "calculated_station_id",
        UUID(as_uuid=True),
        sa.ForeignKey("stations.id"),
        nullable=False,
    ),
    sa.Column(
        "component_station_id",
        UUID(as_uuid=True),
        sa.ForeignKey("stations.id"),
        nullable=False,
    ),
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("weight", sa.Float, nullable=False),  # signed, nonzero, |w| < 1e6
    sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "effective_to", sa.DateTime(timezone=True), nullable=True
    ),  # NULL = current
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
        "calculated_station_id != component_station_id",
        name="ck_csf_distinct_stations",
    ),
    sa.CheckConstraint(
        "weight != 0 AND weight > -1e6 AND weight < 1e6", name="ck_csf_weight_bounds"
    ),
    sa.CheckConstraint(
        "effective_to IS NULL OR effective_to > effective_from",
        name="ck_csf_validity_order",
    ),
)

# Component-lookup index (which calculated stations depend on a component).
sa.Index(
    "ix_csf_component",
    calculated_station_formulas.c.component_station_id,
)
# At most one CURRENT formula row per (calculated station, component, parameter) —
# partial UNIQUE, mirrors the rating_curves active-curve precedent (no btree_gist).
sa.Index(
    "uq_csf_current",
    calculated_station_formulas.c.calculated_station_id,
    calculated_station_formulas.c.component_station_id,
    calculated_station_formulas.c.parameter,
    unique=True,
    postgresql_where=calculated_station_formulas.c.effective_to.is_(None),
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
        # clock_timestamp() (NOT now()/transaction_timestamp()) returns a
        # row-level wall clock, so multiple versions of one logical key
        # inserted in a SINGLE transaction get distinct, insertion-ordered
        # created_at values — making latest-version supersession deterministic.
        server_default=sa.func.clock_timestamp(),
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
        sa.CheckConstraint("artifact_scope IN ('station', 'group', 'virtual')"),
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
    sa.Column("sha256_hash", sa.Text, nullable=False, server_default=""),
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

group_model_assignments = sa.Table(
    "group_model_assignments",
    metadata,
    sa.Column(
        "group_id",
        UUID(as_uuid=True),
        sa.ForeignKey("station_groups.id"),
        nullable=False,
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
    sa.PrimaryKeyConstraint("group_id", "model_id"),
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
        nullable=True,
    ),
    sa.Column("combination_strategy", sa.Text, nullable=True),
    sa.Column("source_model_ids", JSONB, nullable=True),
    sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("nwp_cycle_reference_time", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "nwp_cycle_source",
        sa.Text,
        sa.CheckConstraint(
            "nwp_cycle_source IN ('primary', 'fallback', 'runoff_only')"
        ),
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
    sa.Column(
        "qc_status",
        sa.Text,
        nullable=False,
        server_default="raw",
    ),
    sa.Column("qc_flags", JSONB, nullable=False, server_default="[]"),
    # Plan 035 Task 2: active rating curve for this station at issued_at. NULL
    # for directly-measured-discharge stations. Value set at storage time (Task 4).
    # Same-station enforced by the composite FK below (MATCH SIMPLE → skipped
    # when NULL).
    sa.Column("rating_curve_id", UUID(as_uuid=True), nullable=True),
    sa.ForeignKeyConstraint(
        ["station_id", "rating_curve_id"],
        ["rating_curves.station_id", "rating_curves.id"],
        name="fk_forecasts_rating_curve_station",
    ),
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
    "uq_forecasts_station_model_issued_param",
    forecasts.c.station_id,
    forecasts.c.model_id,
    forecasts.c.issued_at,
    forecasts.c.parameter,
    unique=True,
    postgresql_where=forecasts.c.status != "superseded",
)
# Plan 035 Task 2 — join forecasts back to their rating curve.
sa.Index(
    "ix_forecasts_rating_curve",
    forecasts.c.rating_curve_id,
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
    sa.Column(
        "qc_status",
        sa.Text,
        nullable=False,
        server_default="raw",
    ),
    sa.Column("qc_flags", JSONB, nullable=False, server_default="[]"),
)

# Indexes on hindcast_forecasts
sa.Index(
    "uq_hindcast_forecasts_station_model_step_param_run",
    hindcast_forecasts.c.station_id,
    hindcast_forecasts.c.model_id,
    hindcast_forecasts.c.hindcast_step,
    hindcast_forecasts.c.parameter,
    hindcast_forecasts.c.hindcast_run_id,
    hindcast_forecasts.c.forcing_type,
    unique=True,
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

sa.Index(
    "ix_hindcast_values_forecast_id",
    hindcast_values.c.hindcast_forecast_id,
)

forecast_qc_overrides = sa.Table(
    "forecast_qc_overrides",
    metadata,
    sa.Column(
        "station_id", UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False
    ),
    sa.Column("rule_id", sa.Text, nullable=False),
    sa.Column("parameter", sa.Text, nullable=False),
    sa.Column("time_step_seconds", sa.Integer, nullable=False),
    sa.Column("thresholds", JSONB, nullable=False),
    sa.UniqueConstraint(
        "station_id",
        "rule_id",
        "parameter",
        "time_step_seconds",
        name="uq_forecast_qc_overrides_natural_key",
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
    sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"),
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
        nullable=True,
    ),
    sa.Column("parameter", sa.Text, nullable=False),
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
        nullable=True,
    ),
    sa.Column("parameter", sa.Text, nullable=False),
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
    skill_scores.c.parameter,
    skill_scores.c.skill_source,
    sa.text("COALESCE(forcing_type, '')"),
    skill_scores.c.computation_version,
    skill_scores.c.lead_time_hours,
    sa.text("COALESCE(season, '')"),
    sa.text("COALESCE(flow_regime, '')"),
    skill_scores.c.metric,
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
sa.Index(
    "uq_skill_diagrams_natural_key",
    skill_diagrams.c.station_id,
    skill_diagrams.c.model_artifact_id,
    skill_diagrams.c.parameter,
    skill_diagrams.c.skill_source,
    skill_diagrams.c.computation_version,
    skill_diagrams.c.lead_time_hours,
    sa.text("COALESCE(season, '')"),
    sa.text("COALESCE(flow_regime, '')"),
    skill_diagrams.c.diagram_type,
    sa.text("COALESCE(threshold_level, '')"),
    unique=True,
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
        "model_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    ),
    sa.Column("alert_model_strategy", sa.Text, nullable=True),
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

# Plan 147 Slice B: the append-only audit substrate every audited mutation
# depends on. Conforms EXACTLY to the authoritative contract — no
# `tenant_id`/`action`/`at` columns (tenant context + rejection outcome/
# reason live in `detail`). No FK on `actor_id`: an append-only row must
# survive token revocation/deletion. Append-only is enforced by a
# role-independent DB trigger (migration 0046), NOT by omitting grants here.
audit_log = sa.Table(
    "audit_log",
    metadata,
    sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
    sa.Column(
        "actor_type",
        sa.Text,
        sa.CheckConstraint("actor_type IN ('user', 'api_key', 'system')"),
        nullable=False,
    ),
    sa.Column("target_type", sa.Text, nullable=True),
    sa.Column("target_id", sa.Text, nullable=True),
    sa.Column("detail", JSONB, nullable=True),
    sa.Column("ip_address", INET, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Index("ix_audit_log_created_at", "created_at"),
    sa.Index("ix_audit_log_event_type_created_at", "event_type", "created_at"),
    sa.Index("ix_audit_log_target", "target_type", "target_id"),
    sa.Index("ix_audit_log_actor_id", "actor_id"),
)
