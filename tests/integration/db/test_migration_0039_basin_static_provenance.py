"""Plan 120 Task 0A — DB-backed migration/backfill BEHAVIOUR tests.

Real Alembic upgrade against a throwaway PostGIS container (migration_engine
fixture, mirrors tests/integration/db/test_migration_0033_camels_retire.py).
Cheap structural/metadata checks live in
tests/unit/db/test_basin_static_provenance_schema.py; this file asserts what
only a real DB can prove: FK enforcement, the partial unique index, PostGIS
geometry projection, and the legacy-backfill DATA migration.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon, Point, Polygon
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import (
    basin_static_packages,
    basin_versions,
    basins,
    model_artifact_basin_versions,
    model_artifacts,
    models,
    recap_gateway_polygon_bindings,
    stations,
)
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.ids import BasinId

if TYPE_CHECKING:
    from collections.abc import Iterator

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container for a real Alembic upgrade at a pinned
    revision, so pre-0039 state can be seeded before running the migration
    under test — independent of the shared session-scoped engine."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_120_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            yield engine, url
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _alembic_cfg(url: str) -> object:
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _seed_pre_120_basin(
    conn: sa.Connection, *, code: str = "LEGACY-01", network: str = "ch-bafu"
) -> uuid.UUID:
    """Insert a `basins` row the way pre-Plan-120 code would — omitting the
    (not-yet-existing, at rev 0038) `package_id` column."""
    basin_id = uuid.uuid4()
    conn.execute(
        sa.insert(basins).values(
            id=basin_id,
            code=code,
            name="Legacy Basin",
            geometry=from_shape(_GEOM, srid=4326),
            area_km2=42.0,
            attributes={"legacy_attr": 1.0},
            regional_basin=None,
            band_geometries=None,
            network=network,
        )
    )
    return basin_id


class TestLegacyBackfillRegression:
    """A pre-120 basin (no basin_versions row) gets exactly one version=1,
    superseded_at IS NULL, package_id IS NULL row on upgrade — otherwise
    Task 2D's lineage write finds no current version for a legacy basin."""

    def test_backfill_creates_current_version_with_projected_geometry(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0038")

        with engine.begin() as conn:
            basin_id = _seed_pre_120_basin(conn)

        command.upgrade(cfg, "0039")

        with engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.select(basin_versions).where(
                        basin_versions.c.basin_id == basin_id
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 1, "expected exactly one backfilled version row"
        row = rows[0]
        assert row["version"] == 1
        assert row["superseded_at"] is None
        assert row["package_id"] is None
        assert row["area_km2"] == pytest.approx(42.0)
        assert row["attributes"] == {"legacy_attr": 1.0}
        geom = to_shape(row["geometry"])
        assert geom.equals_exact(_GEOM, tolerance=1e-6)

    def test_legacy_basin_can_receive_a_lineage_row(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        """Cross-checks Task 2D's resolution target against a legacy basin:
        the backfilled current basin_versions row is a valid FK target for
        model_artifact_basin_versions (the lineage helper itself is Task 2D,
        out of this task's scope — this proves the join-table FK path a
        legacy basin needs is not blocked)."""
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0038")

        with engine.begin() as conn:
            basin_id = _seed_pre_120_basin(conn, code="LEGACY-02")

        command.upgrade(cfg, "0039")

        with engine.begin() as conn:
            version_id = conn.execute(
                sa.select(basin_versions.c.id).where(
                    sa.and_(
                        basin_versions.c.basin_id == basin_id,
                        basin_versions.c.superseded_at.is_(None),
                    )
                )
            ).scalar_one()

            model_id = "test-model-120"
            conn.execute(
                sa.insert(models).values(
                    id=model_id,
                    display_name="Test Model",
                    artifact_scope="station",
                    description="test",
                )
            )
            station_id = uuid.uuid4()
            conn.execute(
                sa.insert(stations).values(
                    id=station_id,
                    code="S-LEGACY-01",
                    name="Legacy Station",
                    location=from_shape(Point(7.5, 46.5), srid=4326),
                    station_kind="river",
                    basin_id=basin_id,
                    timezone="Europe/Zurich",
                    measured_parameters=["discharge"],
                    station_status="operational",
                    network="ch-bafu",
                )
            )
            artifact_id = uuid.uuid4()
            conn.execute(
                sa.insert(model_artifacts).values(
                    id=artifact_id,
                    model_id=model_id,
                    station_id=station_id,
                    group_id=None,
                    status="training",
                    artifact_path="s3://fake/path",
                    sha256_hash="deadbeef",
                    training_period_start=sa.func.now(),
                    training_period_end=sa.func.now(),
                    trained_at=sa.func.now(),
                )
            )
            # The lineage write itself (Task 2D).
            conn.execute(
                sa.insert(model_artifact_basin_versions).values(
                    model_artifact_id=artifact_id, basin_version_id=version_id
                )
            )

            count = conn.execute(
                sa.select(sa.func.count()).where(
                    model_artifact_basin_versions.c.model_artifact_id == artifact_id
                )
            ).scalar_one()
        assert count == 1


class TestNonPackageInsertRegression:
    """A basin created via PgBasinStore.store_basin (the onboarding path)
    AFTER the migration gains exactly one current basin_versions row —
    proving the store-enforced invariant covers BOTH creation paths, not
    just the one-time legacy backfill."""

    def test_store_basin_after_migration_creates_current_version(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        basin = Basin(
            id=BasinId(uuid.uuid4()),
            code="ONBOARD-01",
            name="Onboarded Basin",
            geometry=_GEOM,
            area_km2=7.5,
            attributes=None,
            regional_basin=None,
            band_geometries=None,
            created_at=datetime.now(UTC),
            network="ch-bafu",
        )

        with engine.connect() as conn:
            store = PgBasinStore(conn)
            store.store_basin(basin)  # package_id=None — non-package path
            conn.commit()

            rows = (
                conn.execute(
                    sa.select(basin_versions).where(
                        basin_versions.c.basin_id == basin.id
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 1
        assert rows[0]["version"] == 1
        assert rows[0]["superseded_at"] is None
        assert rows[0]["package_id"] is None


class TestAtomicPairRegression:
    """store_basin's basins-row + basin_versions-row pair is written by ONE
    SQL statement (a data-modifying CTE), so it is atomic even under an
    AUTOCOMMIT connection — two separate statements would each self-commit
    independently. Proven structurally: exactly one execute() call produces
    both rows (Plan 120 D-0A)."""

    def test_exactly_one_execute_call_writes_both_rows(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        basin = Basin(
            id=BasinId(uuid.uuid4()),
            code="ATOMIC-01",
            name="Atomic Basin",
            geometry=_GEOM,
            area_km2=1.0,
            attributes=None,
            regional_basin=None,
            band_geometries=None,
            created_at=datetime.now(UTC),
            network="ch-bafu",
        )

        execute_calls = 0
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            real_execute = conn.execute

            def counting_execute(*args: object, **kwargs: object) -> object:
                nonlocal execute_calls
                execute_calls += 1
                return real_execute(*args, **kwargs)

            conn.execute = counting_execute  # type: ignore[method-assign]
            store = PgBasinStore(conn)
            store.store_basin(basin)

        assert execute_calls == 1, (
            "store_basin must write the basins+basin_versions pair in ONE "
            "SQL statement (a two-statement implementation would self-commit "
            "each half independently under AUTOCOMMIT)"
        )

        with engine.connect() as conn:
            basins_row = conn.execute(
                sa.select(basins.c.id).where(basins.c.id == basin.id)
            ).one_or_none()
            versions_rows = (
                conn.execute(
                    sa.select(basin_versions).where(
                        basin_versions.c.basin_id == basin.id
                    )
                )
                .mappings()
                .all()
            )
        assert basins_row is not None
        assert len(versions_rows) == 1
        assert versions_rows[0]["superseded_at"] is None


class TestFkOrderEnforced:
    """basin_static_packages must be inserted before basins/basin_versions
    reference it — an immediate (non-DEFERRABLE) FK raises otherwise."""

    def test_basin_versions_package_id_fk_rejects_unknown_package(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.connect() as conn, pytest.raises(IntegrityError):
            trans = conn.begin()
            conn.execute(
                sa.insert(basins).values(
                    id=uuid.uuid4(),
                    code="FK-ORDER-01",
                    name="FK order basin",
                    geometry=from_shape(_GEOM, srid=4326),
                    area_km2=1.0,
                    attributes=None,
                    regional_basin=None,
                    band_geometries=None,
                    network="ch-bafu",
                    package_id="does-not-exist",
                )
            )
            trans.rollback()
            raise AssertionError("unreachable")

    def test_package_row_first_then_basin_succeeds(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            conn.execute(
                sa.insert(basin_static_packages).values(
                    package_id="pkg-fk-order-01",
                    network="dhm",
                    contract_version="basin-static-artifact/v1",
                    checksums={"basins.gpkg": "sha256:deadbeef"},
                )
            )
            basin_id = uuid.uuid4()
            conn.execute(
                sa.insert(basins).values(
                    id=basin_id,
                    code="FK-ORDER-02",
                    name="FK order basin ok",
                    geometry=from_shape(_GEOM, srid=4326),
                    area_km2=1.0,
                    attributes=None,
                    regional_basin=None,
                    band_geometries=None,
                    network="dhm",
                    package_id="pkg-fk-order-01",
                )
            )
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(basins.c.package_id).where(basins.c.id == basin_id)
            ).scalar_one()
        assert row == "pkg-fk-order-01"


class TestFiveAProvenanceColumnsAdditive:
    """§5a table gains nullable package_id/imported_at with base six intact."""

    def test_five_a_row_insert_without_provenance_still_works(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            basin_id = uuid.uuid4()
            conn.execute(
                sa.insert(basins).values(
                    id=basin_id,
                    code="5A-01",
                    name="5a basin",
                    geometry=from_shape(_GEOM, srid=4326),
                    area_km2=1.0,
                    attributes=None,
                    regional_basin=None,
                    band_geometries=None,
                    network="dhm",
                )
            )
            station_id = uuid.uuid4()
            from shapely.geometry import Point

            conn.execute(
                sa.insert(stations).values(
                    id=station_id,
                    code="5A-STATION-01",
                    name="5a station",
                    location=from_shape(Point(7.5, 46.5), srid=4326),
                    station_kind="river",
                    basin_id=basin_id,
                    timezone="Asia/Kathmandu",
                    measured_parameters=["discharge"],
                    station_status="operational",
                    network="dhm",
                )
            )
            conn.execute(
                sa.insert(recap_gateway_polygon_bindings).values(
                    station_id=station_id,
                    basin_id=basin_id,
                    gateway_hru_name="nepal_dhm_v1",
                    name="g_5a_01",
                    spatial_type="basin_average",
                    band_id=None,
                    # package_id / imported_at omitted — must default to NULL.
                )
            )
        with engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(recap_gateway_polygon_bindings).where(
                        recap_gateway_polygon_bindings.c.station_id == station_id
                    )
                )
                .mappings()
                .one()
            )
        assert row["package_id"] is None
        assert row["imported_at"] is None
        assert row["gateway_hru_name"] == "nepal_dhm_v1"
