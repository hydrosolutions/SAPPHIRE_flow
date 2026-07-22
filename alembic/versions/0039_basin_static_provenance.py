"""basin/static package provenance + versioned basin state (Plan 120 Task 0A)

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-22

Adds the persistence layer docs/requirements/04-basin-static-artifact-contract.md
§5a (`:305-310`) requires before Nepal production enablement — see
docs/plans/120-basin-static-importer.md "Versioned basin state" for the full
design rationale. All additive; no existing column is redefined.

* `basin_static_packages` — provenance for an accepted package (producer-declared
  `package_id`, not a SAP3 UUID).
* `basin_versions` — append-only version history keyed to the STABLE `basins.id`
  (inbound FKs from `stations.basin_id` / the §5a table are never repointed on a
  correction). Partial unique index `uq_basin_versions_one_current_per_basin`
  enforces exactly one `superseded_at IS NULL` row per basin.
* `model_artifact_basin_versions` — lineage join table (station- and group-scoped
  artifacts both span potentially many basin_versions).
* `basins.package_id` / `recap_gateway_polygon_bindings.package_id`+`imported_at` —
  additive nullable provenance columns.
* Legacy backfill (blocker review finding): every PRE-EXISTING `basins` row gets
  exactly one `version=1, superseded_at IS NULL, package_id=NULL` `basin_versions`
  row, snapshotting its current geometry/attributes/area_km2/band_geometries and
  its current §5a rows (if any) into `gateway_mapping` — otherwise a legacy
  (Swiss/CAMELS-CH) basin has no current version and Task 2D's lineage write finds
  nothing to point at.

Ongoing (post-migration) basin inserts are made to also create their `version=1`
row atomically by a code change to `PgBasinStore.store_basin` (this plan, same
task) — NOT by this migration, which only backfills basins that exist at
migration time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_BACKFILL = """
INSERT INTO basin_versions (
    id, basin_id, package_id, version, geometry, attributes, area_km2,
    band_geometries, gateway_mapping, superseded_at, created_at
)
SELECT
    gen_random_uuid(),
    b.id,
    NULL,
    1,
    b.geometry,
    b.attributes,
    b.area_km2,
    b.band_geometries,
    (
        SELECT json_agg(json_build_object(
            'station_id', g.station_id,
            'gateway_hru_name', g.gateway_hru_name,
            'name', g.name,
            'spatial_type', g.spatial_type,
            'band_id', g.band_id
        ))
        FROM recap_gateway_polygon_bindings g
        WHERE g.basin_id = b.id
    ),
    NULL,
    clock_timestamp()
FROM basins b
WHERE NOT EXISTS (
    SELECT 1 FROM basin_versions bv WHERE bv.basin_id = b.id
);
"""


def upgrade() -> None:
    op.create_table(
        "basin_static_packages",
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
    )

    op.add_column(
        "basins",
        sa.Column(
            "package_id",
            sa.Text,
            sa.ForeignKey("basin_static_packages.package_id"),
            nullable=True,
        ),
    )

    op.create_table(
        "basin_versions",
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
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint(
            "basin_id", "version", name="uq_basin_versions_basin_version"
        ),
    )
    op.create_index(
        "uq_basin_versions_one_current_per_basin",
        "basin_versions",
        ["basin_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )

    op.create_table(
        "model_artifact_basin_versions",
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
    # The correction path queries lineage by `basin_version_id`, which the
    # composite PK (model_artifact_id, basin_version_id) cannot serve.
    op.create_index(
        "ix_model_artifact_basin_versions_basin_version_id",
        "model_artifact_basin_versions",
        ["basin_version_id"],
    )

    op.add_column(
        "recap_gateway_polygon_bindings",
        sa.Column(
            "package_id",
            sa.Text,
            sa.ForeignKey("basin_static_packages.package_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "recap_gateway_polygon_bindings",
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(_LEGACY_BACKFILL)


def downgrade() -> None:
    op.drop_column("recap_gateway_polygon_bindings", "imported_at")
    op.drop_column("recap_gateway_polygon_bindings", "package_id")
    op.drop_index(
        "ix_model_artifact_basin_versions_basin_version_id",
        table_name="model_artifact_basin_versions",
    )
    op.drop_table("model_artifact_basin_versions")
    op.drop_index(
        "uq_basin_versions_one_current_per_basin", table_name="basin_versions"
    )
    op.drop_table("basin_versions")
    op.drop_column("basins", "package_id")
    op.drop_table("basin_static_packages")
