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

NOTE for the Plan 115c implementer: ``alembic/versions/0030_weather_source_role.py``
earmarked revision 0032 for the ``station_weather_sources.role`` NOT NULL
tightening. 082 landed first and takes 0032 here (chronological landing
order, same pattern by which 0031 already superseded an earlier prose
earmark) — 115c's migration must take **0033**, not 0032.
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


def downgrade() -> None:
    op.drop_table("recap_gateway_polygon_bindings")
