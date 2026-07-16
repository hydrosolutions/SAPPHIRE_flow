"""Plan 082 Task 2D store tests + Codex review Finding 3 (major).

Finding 3: the §5a table's PK (station_id, gateway_hru_name, name) permits
multiple basin_average rows per station, but GatewayPolygonResolver.resolve
picks basin_average[0] arbitrarily -- a lingering stale row would then be
silently used. A partial UNIQUE index enforces at most one basin_average
row per station at the DB level (invalid states unrepresentable).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy.exc import IntegrityError

from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.station import GatewayPolygonBindingRow
from tests.conftest import make_station_config

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)


def _seed_basin(conn: sa.Connection) -> BasinId:
    basin = Basin(
        id=BasinId(uuid.uuid4()),
        code=f"RECAP-TEST-{uuid.uuid4().hex[:8]}",
        name="Recap Test Basin",
        geometry=_GEOM,
        area_km2=42.0,
        attributes=None,
        regional_basin=None,
        band_geometries=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        network="dhm",
    )
    PgBasinStore(conn).store_basin(basin)
    return basin.id


def _seed_station(conn: sa.Connection, basin_id: BasinId) -> StationId:
    station = make_station_config(
        station_id=StationId(uuid.uuid4()),
        code=f"RECAP-STA-{uuid.uuid4().hex[:8]}",
        network="dhm",
        basin_id=basin_id,
    )
    PgStationStore(conn).store_station(station)
    return station.id


class TestBasinAverageUniquenessConstraint:
    """At most one basin_average binding per station."""

    def test_second_basin_average_binding_for_same_station_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        store = RecapGatewayPolygonStore(db_connection)

        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_v001",
                name="g_5501",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        )

        # A second BASIN_AVERAGE row for the SAME station under a DIFFERENT
        # (gateway_hru_name, name) -- e.g. a lingering stale "g_5501_old"
        # left behind by a re-import -- does not conflict with the table PK,
        # so before Finding 3's fix this insert silently succeeds.
        with pytest.raises(IntegrityError):
            store.store_binding(
                GatewayPolygonBindingRow(
                    station_id=station_id,
                    basin_id=basin_id,
                    gateway_hru_name="hru_dhm_west_v001",
                    name="g_5501_old",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                )
            )

    def test_basin_average_plus_elevation_band_rows_both_allowed(
        self, db_connection: sa.Connection
    ) -> None:
        # Negative control: the constraint is scoped to basin_average ONLY --
        # a station may still carry elevation_band rows alongside its single
        # basin_average row.
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        store = RecapGatewayPolygonStore(db_connection)

        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_v001",
                name="g_5501",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        )
        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_bands_v001",
                name="g_5501_band_1",
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=1,
            )
        )

        rows = store.fetch_bindings_for_station(station_id)
        assert len(rows) == 2

    def test_two_distinct_stations_may_each_have_one_basin_average(
        self, db_connection: sa.Connection
    ) -> None:
        # Negative control: the constraint is per-station, not global.
        # `stations.basin_id` is itself unique, so each station needs its own
        # basin row; the binding row's `basin_id` FK just needs SOME valid
        # basin, so both bindings reuse one.
        binding_basin_id = _seed_basin(db_connection)
        station_a = _seed_station(db_connection, _seed_basin(db_connection))
        station_b = _seed_station(db_connection, _seed_basin(db_connection))
        store = RecapGatewayPolygonStore(db_connection)

        for station_id, name in ((station_a, "g_5501"), (station_b, "g_5502")):
            store.store_binding(
                GatewayPolygonBindingRow(
                    station_id=station_id,
                    basin_id=binding_basin_id,
                    gateway_hru_name="hru_dhm_west_v001",
                    name=name,
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                )
            )

        assert len(store.fetch_bindings_for_station(station_a)) == 1
        assert len(store.fetch_bindings_for_station(station_b)) == 1


class TestFetchBindingsOrdering:
    """Defense-in-depth (Finding 3): deterministic created_at order."""

    def test_fetch_orders_by_created_at_ascending(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import recap_gateway_polygon_bindings

        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)

        # Insert the LATER-created row FIRST (band_id=2), so an unordered
        # fetch would return [2, 1] -- physical/insertion order -- while the
        # fix (ORDER BY created_at) must return [1, 2].
        db_connection.execute(
            sa.insert(recap_gateway_polygon_bindings).values(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_bands_v001",
                name="g_5501_band_2",
                spatial_type="elevation_band",
                band_id=2,
                created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            )
        )
        db_connection.execute(
            sa.insert(recap_gateway_polygon_bindings).values(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_bands_v001",
                name="g_5501_band_1",
                spatial_type="elevation_band",
                band_id=1,
                created_at=datetime(2026, 1, 1, 6, 0, tzinfo=UTC),
            )
        )

        store = RecapGatewayPolygonStore(db_connection)
        rows = store.fetch_bindings_for_station(station_id)

        assert [r.band_id for r in rows] == [1, 2]
