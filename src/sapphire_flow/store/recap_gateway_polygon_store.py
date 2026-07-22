# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import recap_gateway_polygon_bindings
from sapphire_flow.store._helpers import utc_or_none
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import BasinId, PackageId, StationId
from sapphire_flow.types.station import GatewayPolygonBindingRow


class RecapGatewayPolygonStore:
    """SQL-backed §5a mapping-table reader/writer (Plan 082 Task 2D)."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]:
        # Deterministic order (Codex review Finding 3, defense-in-depth
        # alongside the DB partial-unique constraint on basin_average rows
        # below): a lingering stale row must never win an arbitrary fetch
        # order. `StoreBackedGatewayPolygonResolver` still logs a warning if
        # it ever sees >1 basin_average row for one station.
        rows = (
            self._conn.execute(
                sa.select(recap_gateway_polygon_bindings)
                .where(recap_gateway_polygon_bindings.c.station_id == station_id)
                .order_by(recap_gateway_polygon_bindings.c.created_at)
            )
            .mappings()
            .all()
        )
        return [_row_to_binding(row) for row in rows]

    def store_binding(self, binding: GatewayPolygonBindingRow) -> None:
        # Plan 120 Task 2B: a `basin_average` binding is DELETE-then-INSERT
        # per station, never a bare upsert. `store_binding`'s PK-keyed
        # on_conflict_do_update only overwrites a matching
        # (station_id, gateway_hru_name, name) row — a correction package
        # that renames the HRU/name for the same station's basin-average
        # binding would be a NEW key, landing as a second row alongside the
        # stale one and violating
        # `uq_recap_gateway_polygon_bindings_one_basin_average_per_station`.
        # Deleting the station's existing basin_average row first guarantees
        # exactly one survives, even across a rename. `elevation_band` rows
        # are unaffected (multiple bands per station are expected; deferred
        # to the future band-§5a writer per Task 2B scope-out) and keep the
        # PK-keyed upsert.
        if binding.spatial_type == SpatialRepresentation.BASIN_AVERAGE:
            self._conn.execute(
                sa.delete(recap_gateway_polygon_bindings).where(
                    sa.and_(
                        recap_gateway_polygon_bindings.c.station_id
                        == binding.station_id,
                        recap_gateway_polygon_bindings.c.spatial_type
                        == SpatialRepresentation.BASIN_AVERAGE.value,
                    )
                )
            )
            self._conn.execute(
                sa.insert(recap_gateway_polygon_bindings).values(
                    station_id=binding.station_id,
                    basin_id=binding.basin_id,
                    gateway_hru_name=binding.gateway_hru_name,
                    name=binding.name,
                    spatial_type=binding.spatial_type.value,
                    band_id=binding.band_id,
                    package_id=binding.package_id,
                    imported_at=binding.imported_at,
                )
            )
            return

        stmt = (
            pg_insert(recap_gateway_polygon_bindings)
            .values(
                station_id=binding.station_id,
                basin_id=binding.basin_id,
                gateway_hru_name=binding.gateway_hru_name,
                name=binding.name,
                spatial_type=binding.spatial_type.value,
                band_id=binding.band_id,
                package_id=binding.package_id,
                imported_at=binding.imported_at,
            )
            .on_conflict_do_update(
                index_elements=["station_id", "gateway_hru_name", "name"],
                set_={
                    "basin_id": binding.basin_id,
                    "spatial_type": binding.spatial_type.value,
                    "band_id": binding.band_id,
                    "package_id": binding.package_id,
                    "imported_at": binding.imported_at,
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
        package_id=(
            PackageId(row["package_id"]) if row["package_id"] is not None else None
        ),
        imported_at=utc_or_none(row["imported_at"]),
    )
