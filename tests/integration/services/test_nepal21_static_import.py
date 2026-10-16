from __future__ import annotations

import copy
import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import geopandas as gpd
import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.cli.import_basin_package import run_nepal_static_import_transaction
from sapphire_flow.db.metadata import (
    basin_static_packages,
    basins,
    recap_gateway_polygon_bindings,
    station_weather_sources,
    stations,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.nepal21_delivery_authority import NEPAL21_STATION_CODES
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from tests.conftest import DEFAULT_TENANT_ID, make_station_config

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "basin_static"
    / "nepal-dhm-basins"
)

_PACKAGE_ID = "nepal21_20261012"
_HRU = "nepal21_20261012"
_CLOCK = ensure_utc(datetime(2026, 10, 12, tzinfo=UTC))


def _clock():  # noqa: ANN202 - test helper matches production clock seam
    return _CLOCK


def _copy_static_package(
    tmp_path: Path,
    *,
    package_id: str,
    hru: str,
    station_code: str = "225",
) -> Path:
    package_dir = tmp_path / f"pkg-{package_id}"
    shutil.copytree(FIXTURE_DIR, package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["package_id"] = package_id
    manifest["gateway_hru_names"] = [hru]
    manifest_path.write_text(json.dumps(manifest))

    basins_gdf = gpd.read_file(  # pyright: ignore[reportUnknownMemberType]
        package_dir / "basins.gpkg"
    )
    basins_gdf["station_code"] = station_code
    basins_gdf["basin_code"] = station_code
    basins_gdf["name"] = f"g_{station_code}"
    basins_gdf["display_name"] = f"g_{station_code}"
    basins_gdf["gateway_hru_name"] = hru
    (package_dir / "basins.gpkg").unlink()
    basins_gdf.to_file(  # pyright: ignore[reportUnknownMemberType]
        package_dir / "basins.gpkg", driver="GPKG"
    )

    validation_path = package_dir / "validation_report.json"
    validation = json.loads(validation_path.read_text())
    for basin in validation["basins"]:
        basin["station_code"] = station_code
        basin["basin_code"] = station_code
        basin["name"] = f"g_{station_code}"
        basin["gateway_hru_name"] = hru
    validation_path.write_text(json.dumps(validation))

    manifest = json.loads(manifest_path.read_text())
    for filename in manifest["checksums"]:
        digest = hashlib.sha256((package_dir / filename).read_bytes()).hexdigest()
        manifest["checksums"][filename] = f"sha256:{digest}"
    manifest_path.write_text(json.dumps(manifest))
    (package_dir / "checksums.sha256").write_text(
        "".join(
            f"{value.removeprefix('sha256:')}  {filename}\n"
            for filename, value in manifest["checksums"].items()
        )
    )
    return package_dir


def _copy_full21_static_package(tmp_path: Path, *, package_id: str, hru: str) -> Path:
    package_dir = _copy_static_package(tmp_path, package_id=package_id, hru=hru)
    basin_template = cast(
        "dict[str, object]",
        gpd.read_file(package_dir / "basins.gpkg").iloc[0].to_dict(),  # pyright: ignore[reportUnknownMemberType]
    )
    basin_rows = [
        {
            **basin_template,
            "station_code": code,
            "basin_code": code,
            "gauge_id": code,
            "name": f"g_{code.replace('.', '_')}",
            "display_name": f"Synthetic candidate {code}",
        }
        for code in NEPAL21_STATION_CODES
    ]
    (package_dir / "basins.gpkg").unlink()
    gpd.GeoDataFrame(basin_rows, geometry="geometry", crs="EPSG:4326").to_file(  # pyright: ignore[reportUnknownMemberType]
        package_dir / "basins.gpkg", driver="GPKG"
    )
    static_template = pl.read_parquet(package_dir / "static_attributes.parquet").head(1)
    pl.concat(
        [
            static_template.with_columns(pl.lit(code).alias("gauge_id"))
            for code in NEPAL21_STATION_CODES
        ]
    ).write_parquet(package_dir / "static_attributes.parquet")
    validation_path = package_dir / "validation_report.json"
    validation = json.loads(validation_path.read_text())
    template = validation["basins"][0]
    validation["basins"] = [
        {
            **copy.deepcopy(template),
            "station_code": code,
            "basin_code": code,
            "name": f"g_{code.replace('.', '_')}",
        }
        for code in NEPAL21_STATION_CODES
    ]
    validation["summary"]["passed"] = 21
    validation_path.write_text(json.dumps(validation))
    manifest_path = package_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for filename in manifest["checksums"]:
        digest = hashlib.sha256((package_dir / filename).read_bytes()).hexdigest()
        manifest["checksums"][filename] = f"sha256:{digest}"
    manifest_path.write_text(json.dumps(manifest))
    (package_dir / "checksums.sha256").write_text(
        "".join(
            f"{value.removeprefix('sha256:')}  {filename}\n"
            for filename, value in manifest["checksums"].items()
        )
    )
    return package_dir


def _seed_remaining_roster(conn: sa.Connection, *, existing: set[str]) -> None:
    for code in NEPAL21_STATION_CODES:
        if code not in existing:
            _seed_dhm_station(conn, code=code)


def _seed_dhm_station(
    conn: sa.Connection,
    *,
    code: str,
    name: str = "Synthetic DHM station",
    lon: float = 85.0,
    lat: float = 27.0,
) -> StationId:
    station_id = StationId(uuid.uuid4())
    PgStationStore(conn).store_station(
        make_station_config(
            station_id=station_id,
            code=code,
            name=name,
            network="dhm",
            lon=lon,
            lat=lat,
            water_level_datum_masl=123.4,
            wigos_id=f"NP-{code}",
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    return station_id


def _active_gateway_source_rows(station_id: StationId) -> list[dict[str, object]]:
    return [
        {
            "station_id": station_id,
            "nwp_source": "era5_land",
            "extraction_type": "basin_average",
            "status": "active",
            "role": "reanalysis",
        },
        {
            "station_id": station_id,
            "nwp_source": "ifs_ecmwf",
            "extraction_type": "basin_average",
            "status": "active",
            "role": "forecast",
        },
    ]


def _grant_static_importer_bootstrap_privileges(conn: sa.Connection) -> None:
    conn.execute(
        sa.text(
            """
            DO $$
            DECLARE rel text; fn text;
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = 'sapphire_static_importer'
                ) THEN
                    CREATE ROLE sapphire_static_importer NOLOGIN NOSUPERUSER
                        NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
                        NOBYPASSRLS;
                END IF;
                EXECUTE 'GRANT sapphire_static_importer TO ' ||
                    quote_ident(current_user);
                GRANT USAGE ON SCHEMA public TO sapphire_static_importer;
                FOREACH rel IN ARRAY ARRAY['tenants', 'stations', 'basins',
                                          'basin_versions',
                                          'basin_static_packages',
                                          'recap_gateway_polygon_bindings',
                                          'station_weather_sources',
                                          'station_groups',
                                          'station_group_members',
                                          'group_model_assignments',
                                          'model_assignments',
                                          'model_artifacts',
                                          'model_artifact_basin_versions'] LOOP
                    IF to_regclass('public.' || rel) IS NOT NULL THEN
                        EXECUTE format(
                            'GRANT SELECT ON public.%I TO sapphire_static_importer',
                            rel
                        );
                    END IF;
                END LOOP;
                FOREACH rel IN ARRAY ARRAY['basin_static_packages', 'basins',
                                          'basin_versions',
                                          'recap_gateway_polygon_bindings'] LOOP
                    IF to_regclass('public.' || rel) IS NOT NULL THEN
                        EXECUTE format(
                            'GRANT INSERT, UPDATE ON public.%I '
                            'TO sapphire_static_importer',
                            rel
                        );
                    END IF;
                END LOOP;
                IF to_regclass('public.stations') IS NOT NULL THEN
                    EXECUTE 'GRANT UPDATE (basin_id) ON public.stations '
                        'TO sapphire_static_importer';
                END IF;
                FOREACH fn IN ARRAY ARRAY[
                    'public.sf_lock_nepal_static_guard(uuid)'
                ] LOOP
                    IF to_regprocedure(fn) IS NOT NULL THEN
                        EXECUTE format(
                            'GRANT EXECUTE ON FUNCTION %s '
                            'TO sapphire_static_importer',
                            fn
                        );
                    END IF;
                END LOOP;
            END $$
            """
        )
    )


def test_nepal21_static_transaction_persists_static_rows_without_weather_activation(
    db_connection: sa.Connection, tmp_path: Path
) -> None:
    package_dir = _copy_full21_static_package(
        tmp_path, package_id=_PACKAGE_ID, hru=_HRU
    )
    new_station_id = _seed_dhm_station(db_connection, code="225")
    old_station_id = _seed_dhm_station(
        db_connection,
        code="447",
        name="Old six preserved",
        lon=86.1,
        lat=28.2,
    )
    _seed_remaining_roster(db_connection, existing={"225", "447"})
    before_old = (
        db_connection.execute(
            sa.select(
                stations.c.name,
                sa.func.ST_X(stations.c.location).label("lon"),
                sa.func.ST_Y(stations.c.location).label("lat"),
                stations.c.water_level_datum_masl,
                stations.c.wigos_id,
            ).where(stations.c.id == old_station_id)
        )
        .mappings()
        .one()
    )

    old6_before = db_connection.execute(
        sa.text(
            "SELECT code, to_jsonb(s) - 'basin_id' FROM stations s "
            "WHERE code IN ('447','450','604.5','647','670','684') ORDER BY code"
        )
    ).all()
    report = run_nepal_static_import_transaction(
        db_connection,
        tenant_id=TenantId(DEFAULT_TENANT_ID),
        package_dir=package_dir,
        expected_package_id=_PACKAGE_ID,
        expected_hru=_HRU,
        clock=_clock,
    )

    assert report.outcome == "imported"
    assert {b.basin_code for b in report.imported_basins} == set(NEPAL21_STATION_CODES)
    assert (
        db_connection.execute(
            sa.select(basin_static_packages.c.package_id).where(
                basin_static_packages.c.package_id == _PACKAGE_ID
            )
        ).scalar_one()
        == _PACKAGE_ID
    )
    basin_row = (
        db_connection.execute(
            sa.select(basins.c.id, basins.c.package_id).where(
                basins.c.network == "dhm", basins.c.code == "225"
            )
        )
        .mappings()
        .one()
    )
    assert basin_row["package_id"] == _PACKAGE_ID
    assert (
        db_connection.execute(
            sa.select(stations.c.basin_id).where(stations.c.id == new_station_id)
        ).scalar_one()
        == basin_row["id"]
    )
    binding = (
        db_connection.execute(
            sa.select(recap_gateway_polygon_bindings).where(
                recap_gateway_polygon_bindings.c.station_id == new_station_id
            )
        )
        .mappings()
        .one()
    )
    assert binding["package_id"] == _PACKAGE_ID
    assert binding["gateway_hru_name"] == _HRU
    assert binding["name"] == "g_225"
    assert (
        db_connection.execute(
            sa.select(sa.func.count()).select_from(station_weather_sources)
        ).scalar_one()
        == 0
    )
    after_old = (
        db_connection.execute(
            sa.select(
                stations.c.name,
                sa.func.ST_X(stations.c.location).label("lon"),
                sa.func.ST_Y(stations.c.location).label("lat"),
                stations.c.water_level_datum_masl,
                stations.c.wigos_id,
            ).where(stations.c.id == old_station_id)
        )
        .mappings()
        .one()
    )
    assert dict(after_old) == dict(before_old)
    assert (
        db_connection.execute(
            sa.text(
                "SELECT code, to_jsonb(s) - 'basin_id' FROM stations s "
                "WHERE code IN ('447','450','604.5','647','670','684') ORDER BY code"
            )
        ).all()
        == old6_before
    )


def test_nepal21_static_transaction_runs_under_static_importer_role(
    db_connection: sa.Connection, tmp_path: Path
) -> None:
    package_dir = _copy_full21_static_package(
        tmp_path, package_id=_PACKAGE_ID, hru=_HRU
    )
    station_id = _seed_dhm_station(db_connection, code="225")
    _seed_remaining_roster(db_connection, existing={"225"})
    _grant_static_importer_bootstrap_privileges(db_connection)

    db_connection.execute(
        sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
    )
    assert db_connection.execute(sa.text("SELECT session_user")).scalar_one() == (
        "sapphire_static_importer"
    )
    report = run_nepal_static_import_transaction(
        db_connection,
        tenant_id=TenantId(DEFAULT_TENANT_ID),
        package_dir=package_dir,
        expected_package_id=_PACKAGE_ID,
        expected_hru=_HRU,
        clock=_clock,
    )
    db_connection.execute(sa.text("RESET SESSION AUTHORIZATION"))

    assert report.outcome == "imported"
    assert (
        db_connection.execute(
            sa.select(stations.c.basin_id).where(stations.c.id == station_id)
        ).scalar_one()
        is not None
    )


def test_nepal21_static_transaction_refuses_active_era5_ifs_absent_to_present_change(
    db_connection: sa.Connection, tmp_path: Path
) -> None:
    package_dir = _copy_full21_static_package(
        tmp_path, package_id=_PACKAGE_ID, hru=_HRU
    )
    station_id = _seed_dhm_station(db_connection, code="225")
    _seed_remaining_roster(db_connection, existing={"225"})
    db_connection.execute(
        sa.insert(station_weather_sources), _active_gateway_source_rows(station_id)
    )

    try:
        run_nepal_static_import_transaction(
            db_connection,
            tenant_id=TenantId(DEFAULT_TENANT_ID),
            package_dir=package_dir,
            expected_package_id=_PACKAGE_ID,
            expected_hru=_HRU,
            clock=_clock,
        )
    except RuntimeError as exc:
        assert "active ERA5/IFS resolver outcomes" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("static import unexpectedly changed an active resolver")

    assert (
        db_connection.execute(
            sa.select(sa.func.count())
            .select_from(basin_static_packages)
            .where(basin_static_packages.c.package_id == _PACKAGE_ID)
        ).scalar_one()
        == 0
    )
    assert (
        db_connection.execute(
            sa.select(sa.func.count())
            .select_from(recap_gateway_polygon_bindings)
            .where(recap_gateway_polygon_bindings.c.station_id == station_id)
        ).scalar_one()
        == 0
    )


def test_nepal21_static_transaction_refuses_active_era5_ifs_changed_binding(
    db_connection: sa.Connection, tmp_path: Path
) -> None:
    baseline_dir = _copy_full21_static_package(
        tmp_path, package_id="nepal-dhm-basins", hru="nepal_dhm_v1"
    )
    station_id = _seed_dhm_station(db_connection, code="225")
    _seed_remaining_roster(db_connection, existing={"225"})
    baseline_report = run_nepal_static_import_transaction(
        db_connection,
        tenant_id=TenantId(DEFAULT_TENANT_ID),
        package_dir=baseline_dir,
        expected_package_id="nepal-dhm-basins",
        expected_hru="nepal_dhm_v1",
        clock=_clock,
    )
    assert baseline_report.outcome == "imported"
    db_connection.execute(
        sa.insert(station_weather_sources), _active_gateway_source_rows(station_id)
    )
    package_dir = _copy_full21_static_package(
        tmp_path, package_id=_PACKAGE_ID, hru=_HRU
    )

    try:
        run_nepal_static_import_transaction(
            db_connection,
            tenant_id=TenantId(DEFAULT_TENANT_ID),
            package_dir=package_dir,
            expected_package_id=_PACKAGE_ID,
            expected_hru=_HRU,
            clock=_clock,
        )
    except RuntimeError as exc:
        assert "changed" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("static import unexpectedly changed an active resolver")

    binding = db_connection.execute(
        sa.select(
            recap_gateway_polygon_bindings.c.package_id,
            recap_gateway_polygon_bindings.c.gateway_hru_name,
        ).where(recap_gateway_polygon_bindings.c.station_id == station_id)
    ).one()
    assert binding == ("nepal-dhm-basins", "nepal_dhm_v1")


def test_nepal21_static_transaction_refuses_incomplete_roster(
    db_connection: sa.Connection, tmp_path: Path
) -> None:
    package_dir = _copy_static_package(tmp_path, package_id=_PACKAGE_ID, hru=_HRU)
    _seed_dhm_station(db_connection, code="225")
    with pytest.raises(ValueError, match="exactly the approved 21"):
        run_nepal_static_import_transaction(
            db_connection,
            tenant_id=TenantId(DEFAULT_TENANT_ID),
            package_dir=package_dir,
            expected_package_id=_PACKAGE_ID,
            expected_hru=_HRU,
            clock=_clock,
        )


@pytest.mark.parametrize("assignment_scope", ["station", "group"])
def test_full21_static_role_refuses_unresolved_active_assignment(
    db_connection: sa.Connection, tmp_path: Path, assignment_scope: str
) -> None:
    package_dir = _copy_full21_static_package(
        tmp_path, package_id=_PACKAGE_ID, hru=_HRU
    )
    station_id = _seed_dhm_station(db_connection, code="225")
    _seed_remaining_roster(db_connection, existing={"225"})
    db_connection.execute(
        sa.text(
            "INSERT INTO models (id, display_name, artifact_scope, description) "
            "VALUES ('synthetic-missing-model', 'Missing "
            "synthetic model', 'station', 'test only')"
        )
    )
    if assignment_scope == "station":
        db_connection.execute(
            sa.text(
                "INSERT INTO model_assignments (station_id, "
                "model_id, time_step, status) "
                "VALUES (:station, 'synthetic-missing-model', "
                "interval '1 day', 'active')"
            ),
            {"station": station_id},
        )
    else:
        group_id = uuid.uuid4()
        db_connection.execute(
            sa.text(
                "INSERT INTO station_groups (id, name, tenant_id) "
                "VALUES (:id, 'synthetic', :tenant)"
            ),
            {"id": group_id, "tenant": DEFAULT_TENANT_ID},
        )
        db_connection.execute(
            sa.text(
                "INSERT INTO station_group_members (group_id, "
                "station_id, tenant_id) VALUES (:id, :station, :tenant)"
            ),
            {"id": group_id, "station": station_id, "tenant": DEFAULT_TENANT_ID},
        )
        db_connection.execute(
            sa.text(
                "INSERT INTO group_model_assignments (group_id, "
                "model_id, time_step, status) "
                "VALUES (:id, 'synthetic-missing-model', interval '1 day', 'active')"
            ),
            {"id": group_id},
        )
    _grant_static_importer_bootstrap_privileges(db_connection)
    db_connection.execute(
        sa.text("SET LOCAL SESSION AUTHORIZATION sapphire_static_importer")
    )
    try:
        with pytest.raises(ConfigurationError, match="not discovered"):
            run_nepal_static_import_transaction(
                db_connection,
                tenant_id=TenantId(DEFAULT_TENANT_ID),
                package_dir=package_dir,
                expected_package_id=_PACKAGE_ID,
                expected_hru=_HRU,
                clock=_clock,
            )
    finally:
        db_connection.execute(sa.text("RESET SESSION AUTHORIZATION"))
    assert (
        db_connection.scalar(
            sa.select(sa.func.count()).select_from(basin_static_packages)
        )
        == 0
    )
