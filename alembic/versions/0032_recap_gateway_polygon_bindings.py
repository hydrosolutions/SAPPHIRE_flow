"""recap Gateway polygon-reference persistence (Plan 082 Task 2D, contract §5a)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-16

Adds the additive §5a mapping table
(``docs/requirements/04-basin-static-artifact-contract.md`` §5a) SAP3 uses to
map recap Data Gateway forcing columns back to stations/bands:
``station_id, basin_id, gateway_hru_name, name, spatial_type, band_id``,
keyed by ``station_id + gateway_hru_name + name``. Does not touch the
``basins`` table. 082 owns this schema + the store-backed
``GatewayPolygonResolver`` that reads it; Plan 120 (the §5a importer) owns
POPULATING it from an accepted basin/static package.

Also adds a partial UNIQUE index enforcing at most one ``basin_average``
binding per station (Codex review Finding 3): the PK alone permits multiple
``basin_average`` rows per station, which would make
``GatewayPolygonResolver.resolve`` (picks ``basin_average[0]``) silently
arbitrary/stale if a stale row lingers alongside a fresh one. Plan 120's
importer must upsert-REPLACE the basin_average binding for a station, never
accumulate additional rows.

NOTE for the Plan 115c implementer: ``alembic/versions/0030_weather_source_role.py``
earmarked revision 0032 for the ``station_weather_sources.role`` NOT NULL
tightening. 082 landed first and takes 0032 here (chronological landing
order, same pattern by which 0031 already superseded an earlier prose
earmark) — 115c's migration must take the **next free revision at
implementation time**, not a hardcoded number (115b5 / Release B holds the
camels-ch retire migration and will claim a revision when it lands).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recap_gateway_polygon_bindings",
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column(
            "basin_id",
            UUID(as_uuid=True),
            sa.ForeignKey("basins.id"),
            nullable=False,
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
        sa.PrimaryKeyConstraint("station_id", "gateway_hru_name", "name"),
    )
    op.create_index(
        "uq_recap_gateway_polygon_bindings_one_basin_average_per_station",
        "recap_gateway_polygon_bindings",
        ["station_id"],
        unique=True,
        postgresql_where=sa.text("spatial_type = 'basin_average'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_recap_gateway_polygon_bindings_one_basin_average_per_station",
        table_name="recap_gateway_polygon_bindings",
    )
    op.drop_table("recap_gateway_polygon_bindings")
