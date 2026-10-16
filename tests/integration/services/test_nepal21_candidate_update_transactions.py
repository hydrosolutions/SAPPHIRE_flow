from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.cli.import_basin_package import run_nepal_static_import_transaction
from sapphire_flow.services.nepal21_delivery_authority import OLD6_STATION_CODES
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from tests.integration.db.test_migration_nepal21_delivery_authority import (
    _station,  # pyright: ignore[reportPrivateUsage]
    _tenant,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.db.test_role_bootstrap import (
    _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.services.test_nepal21_static_import import (
    _copy_full21_static_package,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def role_harness() -> Iterator[_RoleBootstrapHarness]:
    # These cases commit real transactions; each needs its own disposable DB.
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            command.upgrade(Config("alembic.ini"), "head")
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn:
                conn.execute(sa.text("CREATE DATABASE prefect"))
            yield _RoleBootstrapHarness(postgres, engine)
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def test_candidate_lineage_survives_drained_insert_checks_and_later_transactions(
    role_harness: _RoleBootstrapHarness, tmp_path: Path
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    update = sa.text(
        "UPDATE stations SET basin_id=:basin WHERE code='225' AND network='dhm'"
    )
    reason = "matching fingerprinted package lineage"
    with role_harness.owner_engine.begin() as conn:
        tenant_id = TenantId(_tenant(conn))
        for code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant_id, code=code)
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        run_nepal_static_import_transaction(
            conn,
            tenant_id=tenant_id,
            package_dir=package,
            expected_package_id="nepal21_20261012",
            expected_hru="nepal21_20261012",
            clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
        conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        expected_basin = conn.scalar(
            sa.text("SELECT basin_id FROM stations WHERE code='225'")
        )
        wrong_basin = uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO basins (id,code,name,geometry,network) "
                "SELECT :id,'wrong-lineage','Unbound test basin',geometry,network "
                "FROM basins WHERE id=:existing"
            ),
            {"id": wrong_basin, "existing": expected_basin},
        )
        assert expected_basin is not None and wrong_basin != expected_basin
        for replacement in (None, wrong_basin):
            with pytest.raises(DBAPIError, match=reason), conn.begin_nested():
                conn.execute(update, {"basin": replacement})
    # Insert events are gone: the invariant must also run on subsequent UPDATEs.
    for replacement in (None, wrong_basin):
        with (
            pytest.raises(DBAPIError, match=reason),
            role_harness.owner_engine.begin() as conn,
        ):
            conn.execute(
                sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
            )
            assert (
                conn.scalar(sa.text("SELECT session_user"))
                == "sapphire_static_importer"
            )
            conn.execute(update, {"basin": replacement})
        with role_harness.owner_engine.connect() as conn:
            assert (
                conn.scalar(sa.text("SELECT basin_id FROM stations WHERE code='225'"))
                == expected_basin
            )


_DEPENDENCY_UPDATES = {
    "basin_package": "UPDATE basins SET package_id=NULL WHERE id="
    "(SELECT basin_id FROM stations WHERE code='225')",
    "binding_package": "UPDATE recap_gateway_polygon_bindings SET package_id=NULL "
    "WHERE station_id=(SELECT id FROM stations WHERE code='225')",
    "binding_basin": "UPDATE recap_gateway_polygon_bindings SET basin_id="
    "(SELECT basin_id FROM stations WHERE code='447') "
    "WHERE station_id=(SELECT id FROM stations WHERE code='225')",
    "package_fingerprint": "UPDATE basin_static_packages SET fingerprint=NULL "
    "WHERE package_id='nepal21_20261012'",
    "basin_network": "UPDATE basins SET network='bafu' WHERE id="
    "(SELECT basin_id FROM stations WHERE code='225')",
    "package_checksums": "UPDATE basin_static_packages SET checksums='{}'::jsonb "
    "WHERE package_id='nepal21_20261012'",
    "binding_station": "UPDATE recap_gateway_polygon_bindings SET station_id="
    "(SELECT id FROM stations WHERE code='447'),"
    "spatial_type='elevation_band',band_id=1 "
    "WHERE station_id=(SELECT id FROM stations WHERE code='225')",
}


@pytest.mark.parametrize("mutation", tuple(_DEPENDENCY_UPDATES))
@pytest.mark.parametrize("phase", ["drained", "later"])
def test_dependency_updates_preserve_candidate_lineage(
    role_harness: _RoleBootstrapHarness,
    tmp_path: Path,
    mutation: Literal[
        "basin_package",
        "binding_package",
        "binding_basin",
        "package_fingerprint",
        "basin_network",
        "package_checksums",
        "binding_station",
    ],
    phase: Literal["drained", "later"],
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    statement = sa.text(_DEPENDENCY_UPDATES[mutation])
    with role_harness.owner_engine.begin() as conn:
        tenant_id = TenantId(_tenant(conn))
        for code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant_id, code=code)
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        assert conn.scalar(sa.text("SELECT session_user")) == "sapphire_static_importer"
        for table in (
            "basins",
            "basin_static_packages",
            "recap_gateway_polygon_bindings",
        ):
            assert not conn.scalar(
                sa.text("SELECT has_table_privilege(session_user, :table, 'DELETE')"),
                {"table": table},
            )
        run_nepal_static_import_transaction(
            conn,
            tenant_id=tenant_id,
            package_dir=package,
            expected_package_id="nepal21_20261012",
            expected_hru="nepal21_20261012",
            clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
        if phase == "drained":
            conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            with (
                pytest.raises(
                    DBAPIError, match="matching fingerprinted package lineage"
                ),
                conn.begin_nested(),
            ):
                conn.execute(statement)
    if phase == "later":
        with (
            pytest.raises(DBAPIError, match="matching fingerprinted package lineage"),
            role_harness.owner_engine.begin() as conn,
        ):
            conn.execute(
                sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
            )
            assert (
                conn.scalar(sa.text("SELECT session_user"))
                == "sapphire_static_importer"
            )
            conn.execute(statement)


def test_dependency_checks_read_final_state_and_allow_valid_rebinding(
    role_harness: _RoleBootstrapHarness,
    tmp_path: Path,
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    package = _copy_full21_static_package(
        tmp_path, package_id="nepal21_20261012", hru="nepal21_20261012"
    )
    with role_harness.owner_engine.begin() as conn:
        tenant_id = TenantId(_tenant(conn))
        for code in sorted(OLD6_STATION_CODES):
            _station(conn, tenant_id, code=code)
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        run_nepal_static_import_transaction(
            conn,
            tenant_id=tenant_id,
            package_dir=package,
            expected_package_id="nepal21_20261012",
            expected_hru="nepal21_20261012",
            clock=lambda: ensure_utc(datetime(2026, 10, 12, tzinfo=UTC)),
        )
    with role_harness.owner_engine.begin() as conn:
        conn.execute(
            sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
        )
        assert conn.scalar(sa.text("SELECT session_user")) == "sapphire_static_importer"
        old6_query = sa.text(
            "SELECT to_jsonb(s) FROM stations s WHERE code=ANY(:codes) ORDER BY code"
        )
        old6 = (
            conn.execute(old6_query, {"codes": sorted(OLD6_STATION_CODES)})
            .scalars()
            .all()
        )
        saved = (
            conn.execute(
                sa.text(
                    "SELECT s.id AS station,b.id AS basin,"
                    "p.fingerprint,p.checksums,r.name,"
                    "r.gateway_hru_name FROM stations s "
                    "JOIN basins b ON b.id=s.basin_id "
                    "JOIN basin_static_packages p ON p.package_id=b.package_id "
                    "JOIN recap_gateway_polygon_bindings r ON r.station_id=s.id "
                    "WHERE s.code='225'"
                )
            )
            .mappings()
            .one()
        )
        for statement in _DEPENDENCY_UPDATES.values():
            conn.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
            conn.execute(sa.text(statement))
            conn.execute(
                sa.text(
                    "UPDATE basins SET package_id='nepal21_20261012',network='dhm' "
                    "WHERE id=:basin"
                ),
                {"basin": saved["basin"]},
            )
            conn.execute(
                sa.text(
                    "UPDATE basin_static_packages SET fingerprint=:fingerprint,"
                    "checksums=CAST(:checksums AS jsonb) "
                    "WHERE package_id='nepal21_20261012'"
                ),
                {
                    "fingerprint": saved["fingerprint"],
                    "checksums": json.dumps(saved["checksums"]),
                },
            )
            conn.execute(
                sa.text(
                    "UPDATE recap_gateway_polygon_bindings SET station_id=:station,"
                    "basin_id=:basin,package_id='nepal21_20261012',"
                    "spatial_type='basin_average',band_id=NULL "
                    "WHERE name=:name AND gateway_hru_name=:hru"
                ),
                {
                    "station": saved["station"],
                    "basin": saved["basin"],
                    "name": saved["name"],
                    "hru": saved["gateway_hru_name"],
                },
            )
            conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        conn.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
        rebound = uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO basins (id,code,name,geometry,network,package_id) "
                "SELECT :id,'rebound-candidate','Rebound test basin',"
                "geometry,network,package_id "
                "FROM basins WHERE id=:original"
            ),
            {"id": rebound, "original": saved["basin"]},
        )
        conn.execute(
            sa.text("UPDATE stations SET basin_id=:id WHERE id=:station"),
            {"id": rebound, "station": saved["station"]},
        )
        conn.execute(
            sa.text(
                "UPDATE recap_gateway_polygon_bindings SET basin_id=:id "
                "WHERE station_id=:station"
            ),
            {"id": rebound, "station": saved["station"]},
        )
        conn.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert (
            conn.execute(old6_query, {"codes": sorted(OLD6_STATION_CODES)})
            .scalars()
            .all()
            == old6
        )
    with role_harness.owner_engine.connect() as conn:
        assert (
            conn.scalar(sa.text("SELECT basin_id FROM stations WHERE code='225'"))
            == rebound
        )
