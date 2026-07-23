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
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import BasinId, PackageId, StationId
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

    def test_raw_second_basin_average_insert_for_same_station_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        """DB-level defense-in-depth: the partial unique index itself still
        guards a caller that bypasses `store_binding` (e.g. a raw INSERT).
        `store_binding`'s replace path (below) upserts THROUGH this same
        index via `ON CONFLICT`, but a raw second INSERT that doesn't go
        through the upsert must still be rejected."""
        from sapphire_flow.db.metadata import recap_gateway_polygon_bindings

        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)

        db_connection.execute(
            sa.insert(recap_gateway_polygon_bindings).values(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_v001",
                name="g_5501",
                spatial_type="basin_average",
                band_id=None,
            )
        )

        with pytest.raises(IntegrityError):
            db_connection.execute(
                sa.insert(recap_gateway_polygon_bindings).values(
                    station_id=station_id,
                    basin_id=basin_id,
                    gateway_hru_name="hru_dhm_west_v001",
                    name="g_5501_old",
                    spatial_type="basin_average",
                    band_id=None,
                )
            )

    def test_store_binding_replaces_renamed_basin_average_row(
        self, db_connection: sa.Connection
    ) -> None:
        """Plan 120 Task 2B: a correction package that renames a station's
        basin-average HRU/name must leave exactly one basin_average row --
        not raise `IntegrityError`, not accumulate a stale second row."""
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

        # A correction package renames the HRU/name for the SAME station's
        # basin-average binding -- a new PK, which the old bare-upsert path
        # would insert alongside the stale row and violate the partial
        # unique index. The replace path must not raise, and must leave
        # exactly one basin_average row (the new one).
        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_v002",
                name="g_5501_new",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        )

        rows = store.fetch_bindings_for_station(station_id)
        basin_average_rows = [
            r for r in rows if r.spatial_type == SpatialRepresentation.BASIN_AVERAGE
        ]
        assert len(basin_average_rows) == 1
        assert basin_average_rows[0].gateway_hru_name == "hru_dhm_west_v002"
        assert basin_average_rows[0].name == "g_5501_new"

    def test_store_binding_replace_leaves_old_row_intact_on_insert_failure(
        self, db_engine: sa.Engine
    ) -> None:
        """Codex review (Plan 120 fixer round, major): the replace path is
        DELETE-then-INSERT on an AUTOCOMMIT production connection -- a
        failure on the INSERT half (e.g. an invalid `package_id` FK) used to
        leave the DELETE already committed, silently dropping the station's
        §5a binding. The fix makes the replace a SINGLE `INSERT ... ON
        CONFLICT DO UPDATE` statement, so a mid-statement failure leaves the
        ORIGINAL row completely untouched -- not deleted, not partially
        updated.

        This test deliberately uses a raw AUTOCOMMIT connection (matching
        `setup_production_stores`), NOT the `db_connection` fixture's
        transaction-per-test isolation: wrapping the failing call in a
        SAVEPOINT (as the rest of this file does for failure injection)
        would roll back the DELETE too, masking exactly the AUTOCOMMIT-only
        bug this test exists to catch."""
        from sapphire_flow.db.metadata import (
            basin_versions,
            basins,
            recap_gateway_polygon_bindings,
        )
        from sapphire_flow.db.metadata import stations as stations_table

        with db_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            basin_id = _seed_basin(conn)
            station_id = _seed_station(conn, basin_id)
            store = RecapGatewayPolygonStore(conn)
            try:
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

                bogus_package_id = f"pkg-does-not-exist-{uuid.uuid4().hex[:8]}"
                with pytest.raises(IntegrityError):
                    store.store_binding(
                        GatewayPolygonBindingRow(
                            station_id=station_id,
                            basin_id=basin_id,
                            gateway_hru_name="hru_dhm_west_v002",
                            name="g_5501_new",
                            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                            band_id=None,
                            package_id=PackageId(bogus_package_id),
                        )
                    )

                rows = store.fetch_bindings_for_station(station_id)
                basin_average_rows = [
                    r
                    for r in rows
                    if r.spatial_type == SpatialRepresentation.BASIN_AVERAGE
                ]
                assert len(basin_average_rows) == 1
                assert basin_average_rows[0].gateway_hru_name == "hru_dhm_west_v001"
                assert basin_average_rows[0].name == "g_5501"
                assert basin_average_rows[0].package_id is None
            finally:
                conn.execute(
                    sa.delete(recap_gateway_polygon_bindings).where(
                        recap_gateway_polygon_bindings.c.station_id == station_id
                    )
                )
                conn.execute(
                    sa.delete(stations_table).where(stations_table.c.id == station_id)
                )
                conn.execute(
                    sa.delete(basin_versions).where(
                        basin_versions.c.basin_id == basin_id
                    )
                )
                conn.execute(sa.delete(basins).where(basins.c.id == basin_id))

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


def _seed_package(conn: sa.Connection, package_id: str) -> None:
    from sapphire_flow.db.metadata import basin_static_packages

    conn.execute(
        sa.insert(basin_static_packages).values(
            package_id=package_id,
            network="dhm",
            contract_version="basin-static-artifact/v1",
            checksums={"basins.gpkg": "sha256:deadbeef"},
        )
    )


class TestProvenanceWritePath:
    """Plan 120 Task 2B (major finding): `package_id`/`imported_at` must
    round-trip through `store_binding`, including on a re-population
    (`on_conflict_do_update`)."""

    def test_store_binding_writes_provenance_columns(
        self, db_connection: sa.Connection
    ) -> None:
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        package_id = f"pkg-{uuid.uuid4().hex[:8]}"
        _seed_package(db_connection, package_id)
        imported_at = ensure_utc(datetime(2026, 7, 22, 12, 0, tzinfo=UTC))
        store = RecapGatewayPolygonStore(db_connection)

        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_v001",
                name="g_5501",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                package_id=PackageId(package_id),
                imported_at=imported_at,
            )
        )

        rows = store.fetch_bindings_for_station(station_id)
        assert len(rows) == 1
        assert rows[0].package_id == package_id
        assert rows[0].imported_at == imported_at

    def test_elevation_band_upsert_refreshes_provenance(
        self, db_connection: sa.Connection
    ) -> None:
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        old_package_id = f"pkg-{uuid.uuid4().hex[:8]}"
        new_package_id = f"pkg-{uuid.uuid4().hex[:8]}"
        _seed_package(db_connection, old_package_id)
        _seed_package(db_connection, new_package_id)
        store = RecapGatewayPolygonStore(db_connection)

        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_bands_v001",
                name="g_5501_band_1",
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=1,
                package_id=PackageId(old_package_id),
                imported_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            )
        )
        new_imported_at = ensure_utc(datetime(2026, 7, 22, tzinfo=UTC))
        store.store_binding(
            GatewayPolygonBindingRow(
                station_id=station_id,
                basin_id=basin_id,
                gateway_hru_name="hru_dhm_west_bands_v001",
                name="g_5501_band_1",
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=1,
                package_id=PackageId(new_package_id),
                imported_at=new_imported_at,
            )
        )

        rows = store.fetch_bindings_for_station(station_id)
        assert len(rows) == 1
        assert rows[0].package_id == new_package_id
        assert rows[0].imported_at == new_imported_at


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
