# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import recap_gateway_polygon_bindings
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import GatewayPolygonBindingRow


class RecapGatewayPolygonStore:
    """SQL-backed §5a mapping-table reader/writer (Plan 082 Task 2D)."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]:
        rows = (
            self._conn.execute(
                sa.select(recap_gateway_polygon_bindings).where(
                    recap_gateway_polygon_bindings.c.station_id == station_id
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_binding(row) for row in rows]

    def store_binding(self, binding: GatewayPolygonBindingRow) -> None:
        stmt = (
            pg_insert(recap_gateway_polygon_bindings)
            .values(
                station_id=binding.station_id,
                basin_id=binding.basin_id,
                gateway_hru_name=binding.gateway_hru_name,
                name=binding.name,
                spatial_type=binding.spatial_type.value,
                band_id=binding.band_id,
            )
            .on_conflict_do_update(
                index_elements=["station_id", "gateway_hru_name", "name"],
                set_={
                    "basin_id": binding.basin_id,
                    "spatial_type": binding.spatial_type.value,
                    "band_id": binding.band_id,
                },
            )
        )
        self._conn.execute(stmt)


def _row_to_binding(row: sa.engine.row.RowMapping) -> GatewayPolygonBindingRow:
    return GatewayPolygonBindingRow(
        station_id=StationId(row["station_id"]),
        basin_id=BasinId(row["basin_id"]),
        gateway_hru_name=row["gateway_hru_name"],
        name=row["name"],
        spatial_type=SpatialRepresentation(row["spatial_type"]),
        band_id=row["band_id"],
    )
