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
        # Plan 120 Task 2B (Codex review fixer round, major): a `basin_average`
        # binding must replace the station's existing row -- including across
        # a gateway_hru_name/name rename -- in ONE atomic statement, not a
        # DELETE followed by a separate INSERT `execute()` call. The prior
        # two-statement replace ran on an AUTOCOMMIT connection
        # (`setup_production_stores`): a failure on the INSERT half (e.g. an
        # invalid `package_id` FK) left the DELETE already committed, so the
        # station silently lost its §5a binding and 082's resolver started
        # returning None. The unique index
        # `uq_recap_gateway_polygon_bindings_one_basin_average_per_station`
        # is a partial UNIQUE index on `station_id` alone (not the PK), so a
        # single `INSERT ... ON CONFLICT (station_id) WHERE spatial_type =
        # 'basin_average' DO UPDATE` targets that index directly: Postgres
        # either commits the whole replace or none of it. `elevation_band`
        # rows are unaffected (multiple bands per station are expected;
        # deferred to the future band-§5a writer per Task 2B scope-out) and
        # keep the PK-keyed upsert below.
        if binding.spatial_type == SpatialRepresentation.BASIN_AVERAGE:
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
                    # Partial UNIQUE INDEX (not a named table CONSTRAINT) --
                    # Postgres' `ON CONFLICT ON CONSTRAINT <name>` only
                    # resolves actual constraints, so the conflict target
                    # must be inferred via index_elements + a WHERE clause
                    # matching the index's own partial predicate exactly.
                    index_elements=[recap_gateway_polygon_bindings.c.station_id],
                    index_where=(
                        recap_gateway_polygon_bindings.c.spatial_type
                        == SpatialRepresentation.BASIN_AVERAGE.value
                    ),
                    set_={
                        "basin_id": binding.basin_id,
                        "gateway_hru_name": binding.gateway_hru_name,
                        "name": binding.name,
                        "band_id": binding.band_id,
                        "package_id": binding.package_id,
                        "imported_at": binding.imported_at,
                    },
                )
            )
            self._conn.execute(stmt)
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
