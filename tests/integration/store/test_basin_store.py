from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Point, Polygon
from sqlalchemy.exc import IntegrityError

from sapphire_flow.db.metadata import (
    basin_static_packages,
    basin_versions,
    basins,
    stations,
)
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import BasinId, PackageId

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)


def _make_basin(
    *,
    code: str = "TEST-01",
    network: str = "ch-bafu",
    name: str = "Test Basin",
    regional_basin: str | None = None,
    package_id: PackageId | None = None,
) -> Basin:
    return Basin(
        id=BasinId(uuid.uuid4()),
        code=code,
        name=name,
        geometry=_GEOM,
        area_km2=123.4,
        attributes={"source": "test"},
        regional_basin=regional_basin,
        band_geometries=None,
        created_at=__import__("datetime").datetime(
            2024, 1, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        network=network,
        package_id=package_id,
    )


def _seed_package(conn: sa.Connection, package_id: str) -> PackageId:
    conn.execute(
        basin_static_packages.insert().values(
            package_id=package_id,
            network="ch-bafu",
            contract_version="1.0",
            checksums={},
        )
    )
    return PackageId(package_id)


class TestPgBasinStore:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin()

        returned_id = store.store_basin(basin)
        assert returned_id == basin.id

        fetched = store.fetch_basin(basin.id)
        assert fetched is not None
        assert fetched.id == basin.id
        assert fetched.code == basin.code
        assert fetched.name == basin.name
        assert fetched.network == basin.network
        assert fetched.area_km2 == pytest.approx(123.4)
        assert fetched.attributes == {"source": "test"}
        assert fetched.band_geometries is None
        assert fetched.regional_basin is None
        assert fetched.geometry.geom_type == "MultiPolygon"
        assert fetched.geometry.equals_exact(_GEOM, tolerance=1e-6)

    def test_fetch_by_code(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin(code="MYCODE", network="ch-bafu")
        store.store_basin(basin)

        fetched = store.fetch_basin_by_code("MYCODE", "ch-bafu")
        assert fetched is not None
        assert fetched.id == basin.id
        assert fetched.code == "MYCODE"
        assert fetched.network == "ch-bafu"

    def test_fetch_by_code_not_found(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        result = store.fetch_basin_by_code("DOES-NOT-EXIST", "ch-bafu")
        assert result is None

    def test_fetch_all(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        b1 = _make_basin(code="A1", network="ch-bafu", name="Basin A")
        b2 = _make_basin(code="B2", network="ch-bafu", name="Basin B")
        store.store_basin(b1)
        store.store_basin(b2)

        all_basins = store.fetch_all_basins()
        ids = {b.id for b in all_basins}
        assert b1.id in ids
        assert b2.id in ids

    def test_regional_basin_round_trip(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)

        with_region = _make_basin(code="RB-01", regional_basin="Aare")
        store.store_basin(with_region)
        fetched = store.fetch_basin(with_region.id)
        assert fetched is not None
        assert fetched.regional_basin == "Aare"

        without_region = _make_basin(code="RB-02")
        store.store_basin(without_region)
        fetched_none = store.fetch_basin(without_region.id)
        assert fetched_none is not None
        assert fetched_none.regional_basin is None

    def test_unique_station_basin_id(self, db_connection: sa.Connection) -> None:
        store = PgBasinStore(db_connection)
        basin = _make_basin(code="UQ-01")
        store.store_basin(basin)

        _point = from_shape(Point(7.5, 46.5), srid=4326)
        import datetime

        now = datetime.datetime.now(datetime.UTC)

        def _station_row(code: str) -> dict:
            return {
                "id": uuid.uuid4(),
                "code": code,
                "name": f"Station {code}",
                "location": _point,
                "station_kind": "river",
                "basin_id": basin.id,
                "timezone": "Europe/Zurich",
                "measured_parameters": ["discharge"],
                "station_status": "operational",
                "network": "ch-bafu",
                "ownership": "foreign",
                "created_at": now,
                "updated_at": now,
            }

        db_connection.execute(stations.insert().values(**_station_row("S-UQ-01")))

        with pytest.raises(IntegrityError):
            db_connection.execute(stations.insert().values(**_station_row("S-UQ-02")))


class TestStoreBasinPackageId:
    def test_field_package_id_persists_without_kwarg(
        self, db_connection: sa.Connection
    ) -> None:
        """A Basin carrying its own `package_id` must persist that id to BOTH
        `basins.package_id` AND the paired `basin_versions.package_id`, even
        when `store_basin` is called with NO `package_id` kwarg (the regression
        — the field was previously dropped, writing NULL to both)."""
        pkg = _seed_package(db_connection, "pkg-field-01")
        store = PgBasinStore(db_connection)
        basin = _make_basin(code="PKG-FIELD-01", package_id=pkg)

        store.store_basin(basin)  # no package_id kwarg

        basins_pkg = db_connection.execute(
            sa.select(basins.c.package_id).where(basins.c.id == basin.id)
        ).scalar_one()
        assert basins_pkg == pkg

        version_pkg = db_connection.execute(
            sa.select(basin_versions.c.package_id).where(
                basin_versions.c.basin_id == basin.id
            )
        ).scalar_one()
        assert version_pkg == pkg

    def test_conflicting_kwarg_and_field_raises(
        self, db_connection: sa.Connection
    ) -> None:
        _seed_package(db_connection, "pkg-a")
        kwarg_pkg = _seed_package(db_connection, "pkg-b")
        store = PgBasinStore(db_connection)
        basin = _make_basin(code="PKG-CONFLICT-01", package_id=PackageId("pkg-a"))

        with pytest.raises(ValueError, match="conflicting package_id"):
            store.store_basin(basin, package_id=kwarg_pkg)


class TestUpdateBasinFromPackageAtomicity:
    """Fixer round (major finding — Codex review of Plan 120 Phase 2): the
    correction branch's stamp/append/refresh triple must be ONE atomic
    statement, not three separate `execute()` calls — mirroring
    `store_basin`'s (Task 0A) single-CTE precedent. The old three-statement
    form, run on a raw AUTOCOMMIT connection (`setup_production_stores`),
    could leave the STAMP (superseded_at set on the prior current row)
    permanently committed with no replacement row if the append (an
    IMMEDIATE FK to `basin_static_packages`) then failed — a basin left with
    ZERO current `basin_versions` rows.

    This test deliberately uses a raw AUTOCOMMIT connection, NOT the
    `db_connection` fixture's transaction-per-test isolation: wrapping the
    failing call in a SAVEPOINT would roll back the stamp too, masking
    exactly the AUTOCOMMIT-only bug this test exists to catch — same
    reasoning as
    `test_recap_gateway_polygon_store.py::test_store_binding_replace_leaves_old_row_intact_on_insert_failure`.
    """

    def test_correction_failure_on_autocommit_leaves_original_current_version_intact(
        self, db_engine: sa.Engine
    ) -> None:
        with db_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            code = f"ATOMIC-{uuid.uuid4().hex[:8]}"
            pkg = _seed_package(conn, f"pkg-atomic-{uuid.uuid4().hex[:8]}")
            store = PgBasinStore(conn)
            basin = _make_basin(code=code, package_id=pkg)
            store.store_basin(basin)

            original_version_id = conn.execute(
                sa.select(basin_versions.c.id).where(
                    sa.and_(
                        basin_versions.c.basin_id == basin.id,
                        basin_versions.c.superseded_at.is_(None),
                    )
                )
            ).scalar_one()

            bogus_package_id = PackageId(f"pkg-does-not-exist-{uuid.uuid4().hex[:8]}")
            with pytest.raises(IntegrityError):
                store.update_basin_from_package(
                    basin_id=basin.id,
                    package_id=bogus_package_id,  # IMMEDIATE FK -> append fails
                    name="Corrected Name",
                    geometry=_GEOM,
                    attributes={"corrected": True},
                    area_km2=999.0,
                    regional_basin=None,
                    band_geometries=None,
                    gateway_mapping=None,
                    superseded_at=ensure_utc(datetime(2026, 6, 1, tzinfo=UTC)),
                )

            current_rows = (
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(basin_versions)
                    .where(
                        sa.and_(
                            basin_versions.c.basin_id == basin.id,
                            basin_versions.c.superseded_at.is_(None),
                        )
                    )
                )
            ).scalar_one()
            # Never zero (the orphan hazard) and never two (a stray extra row).
            assert current_rows == 1

            current_id = conn.execute(
                sa.select(basin_versions.c.id).where(
                    sa.and_(
                        basin_versions.c.basin_id == basin.id,
                        basin_versions.c.superseded_at.is_(None),
                    )
                )
            ).scalar_one()
            assert current_id == original_version_id  # the stamp did NOT apply

            projected_area = conn.execute(
                sa.select(basins.c.area_km2).where(basins.c.id == basin.id)
            ).scalar_one()
            assert projected_area == pytest.approx(123.4)  # basins projection untouched
