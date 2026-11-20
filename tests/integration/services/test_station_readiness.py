from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.cli import create_station as cs
from sapphire_flow.cli import station_readiness as cli
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    QcStatus,
    SpatialRepresentation,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_observation, make_station_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from sapphire_flow.types.station import StationConfig

NOW = ensure_utc(datetime(2026, 11, 18, 12, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"sandbox"}), global_admin=False
)


def _create(conn: sa.Connection, code: str) -> None:
    PgTenantStore(conn).ensure_tenant(
        tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
    ) if code == "R1" else None
    argv = [
        "--code", code, "--network", "research", "--name", f"River {code}",
        "--lon", "1", "--lat", "1", "--timezone", "UTC",
        "--parameters", "discharge", "--gateway-name", code.lower(),
        "--tenant", "sandbox", "--apply",
    ]  # fmt: skip
    assert (
        cs.run_create(
            cs._parse_args(argv),  # pyright: ignore[reportPrivateUsage]
            identity=IDENTITY,
            tenants=PgTenantStore(conn),
            stations=PgStationStore(conn),
            audit=PgAuditLogStore(conn),
            ask=None,
            new_id=uuid4,
            clock=lambda: NOW,
        )
        == 0
    )


def _run(conn: sa.Connection, code: str, *extra: str) -> int:
    args = cli._build_parser().parse_args(  # pyright: ignore[reportPrivateUsage]
        ["--tenant", "sandbox", "--network", "research", "--code", code, *extra]
    )
    return cli.run_readiness(
        args,
        identity=IDENTITY,
        tenants=PgTenantStore(conn),
        stores=ReadinessStores(
            stations=PgStationStore(conn),
            basins=PgBasinStore(conn),
            observations=PgObservationStore(conn),
            forcing=PgHistoricalForcingStore(conn),
            ratings=PgRatingCurveStore(conn),
            groups=PgStationGroupStore(conn),
            polygons=RecapGatewayPolygonStore(conn),
        ),
        model=None,
        scope=None,
        clock=lambda: NOW,
    )


def _snapshot(conn: sa.Connection) -> tuple[object, ...]:
    counts = [
        conn.scalar(sa.text(f"SELECT count(*) FROM {t}"))  # noqa: S608
        for t in ("audit_log", "observations", "stations", "station_weather_sources")
    ]
    updated = conn.scalar(sa.text("SELECT max(updated_at) FROM stations"))
    return (*counts, updated)


class TestStationReadinessPg:
    def test_reports_real_aggregates_and_writes_nothing(
        self, db_connection: sa.Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _create(db_connection, "R1")
        station = PgStationStore(db_connection).fetch_station_by_code("R1", "research")
        assert station is not None
        rng = random.Random(3)
        PgObservationStore(db_connection).store_observations(
            [
                make_observation(
                    station_id=station.id,
                    value=1.0 + i,
                    timestamp=ensure_utc(NOW - timedelta(days=i + 1)),
                    qc_status=QcStatus.QC_PASSED if i else QcStatus.QC_UNCHECKED,
                    rng=rng,
                )
                for i in range(10)
            ]
        )
        before = _snapshot(db_connection)
        capsys.readouterr()

        assert _run(db_connection, "R1") == 0

        out = capsys.readouterr().out.splitlines()
        by_name = {line.split("  ")[1]: line for line in out[1:-1]}
        assert by_name["observations"].startswith("PASS  observations  discharge n=10")
        assert "qc_passed=90% qc_unchecked=10%" in by_name["observations"]
        assert by_name["discharge_lookback"].startswith(
            "FAIL  discharge_lookback  10 of 30 days usable"
        )
        assert by_name["basin"].startswith("FAIL")
        assert by_name["datum"].startswith("NOT_APPLICABLE")
        assert out[-1].endswith(" passed")
        assert _snapshot(db_connection) == before

    def test_station_with_zero_observations(
        self, db_connection: sa.Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _create(db_connection, "R1")
        capsys.readouterr()
        assert _run(db_connection, "R1", "--json") == 0
        data = json.loads(capsys.readouterr().out)
        status = {c["name"]: c["status"] for c in data["checks"]}
        assert status["observations"] == "FAIL"
        assert status["discharge_series"] == "FAIL"
        assert status["weather_history"] == "FAIL"
        assert data["failed"] >= 3


class TestWeatherWaitFactsPg:
    def test_reads_stored_reanalysis_days_and_binding_state(
        self, db_connection: sa.Connection, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _create(db_connection, "R1")
        stations = PgStationStore(db_connection)
        station = stations.fetch_station_by_code("R1", "research")
        assert station is not None
        stations.store_weather_source(
            StationWeatherSource(
                station_id=station.id,
                nwp_source="era5_land",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        PgHistoricalForcingStore(db_connection).store_forcing(
            [
                RawHistoricalForcing(
                    station_id=station.id,
                    source="era5_land",
                    version="v1",
                    valid_time=ensure_utc(
                        datetime(2026, 11, 17, tzinfo=UTC) - timedelta(days=i)
                    ),
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=None,
                    value=1.0,
                )
                for i in range(28)
            ]
        )
        before = _snapshot(db_connection)
        capsys.readouterr()

        assert _run(db_connection, "R1", "--json") == 0

        data = json.loads(capsys.readouterr().out)
        (line,) = [c for c in data["checks"] if c["name"] == "weather_wait_facts"]
        assert line["status"] == "FAIL"
        assert "reanalysis=bound" in line["detail"]
        assert "forecast=missing" in line["detail"]
        assert (
            "precipitation missing 2 of 23 days: 2026-10-19, 2026-10-20"
            in (line["detail"])
        )
        assert "temperature missing 23 of 23 days" in line["detail"]
        assert _snapshot(db_connection) == before


class TestSummariesAgainstPostgres:
    def _station(self, conn: sa.Connection) -> StationConfig:
        _create(conn, "R1")
        station = PgStationStore(conn).fetch_station_by_code("R1", "research")
        assert station is not None
        return station

    def test_observation_summary_min_max(self, db_connection: sa.Connection) -> None:
        station = self._station(db_connection)
        rng = random.Random(5)
        obs = [
            make_observation(
                station_id=station.id,
                value=1.0 + i,
                timestamp=ensure_utc(NOW - timedelta(days=i + 1)),
                rng=rng,
            )
            for i in range(5)
        ]
        store = PgObservationStore(db_connection)
        store.store_observations(obs)
        (row,) = store.fetch_station_summary(station.id)
        assert (row.count, row.first, row.last) == (
            5,
            obs[-1].timestamp,
            obs[0].timestamp,
        )

    def test_forcing_summary_counts_distinct_valid_times_across_versions(
        self, db_connection: sa.Connection
    ) -> None:
        station = self._station(db_connection)
        forcing = PgHistoricalForcingStore(db_connection)
        forcing.store_forcing(
            [
                RawHistoricalForcing(
                    station_id=station.id,
                    source="era5_land",
                    version=version,
                    valid_time=ensure_utc(NOW - timedelta(days=i)),
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=None,
                    value=1.0,
                )
                for version in ("v1", "v2")
                for i in range(5)
            ]
        )
        (row,) = forcing.fetch_station_summary(station.id)
        assert row.count == 5
        assert row.first == ensure_utc(NOW - timedelta(days=4))
        assert row.last == NOW


class TestMainIsReadOnly:
    def test_real_main_cannot_write_and_leaves_rows_unchanged(
        self,
        db_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = tmp_path / "config.toml"
        config.write_text('[deployment]\nwritable_tenants = ["rotest"]\n')
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        # main() writes os.environ["SAPPHIRE_CONFIG"]; register it so it is restored
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
        with db_engine.begin() as setup:
            PgTenantStore(setup).ensure_tenant(
                tenant_id=TenantId(uuid4()), code="rotest", name="Ro"
            )
            tenant = PgTenantStore(setup).fetch_tenant_by_code("rotest")
            assert tenant is not None
            PgStationStore(setup).store_station(
                make_station_config(
                    code="RO1", network="rotest", tz="UTC", tenant_id=tenant.id
                )
            )
        write_errors: list[type[BaseException]] = []
        real_run = cli.run_readiness

        def run_then_try_a_write(*a: object, **kw: object) -> int:
            code = real_run(*a, **kw)  # type: ignore[arg-type]
            stores: ReadinessStores = kw["stores"]  # type: ignore[assignment]
            station = stores.stations.fetch_station_by_code("RO1", "rotest")
            assert station is not None
            try:
                stores.stations.update_station_status(
                    station.id, StationStatus.OPERATIONAL
                )
            except sa.exc.DBAPIError as exc:
                write_errors.append(type(exc.orig))  # type: ignore[arg-type]
            return code

        monkeypatch.setattr(cli, "run_readiness", run_then_try_a_write)
        try:
            with db_engine.connect() as probe:
                before = _snapshot(probe)
            capsys.readouterr()
            code = cli.main(
                [
                    "--config",
                    str(config),
                    "--tenant",
                    "rotest",
                    "--network",
                    "rotest",
                    "--code",
                    "RO1",
                ]  # fmt: skip
            )
            assert code == 0
            assert [e.__name__ for e in write_errors] == ["ReadOnlySqlTransaction"]
            assert capsys.readouterr().out.splitlines()[-1].endswith(" passed")
            with db_engine.connect() as probe:
                assert _snapshot(probe) == before
        finally:
            with db_engine.begin() as cleanup:
                cleanup.execute(
                    sa.text("DELETE FROM stations WHERE network = 'rotest'")
                )
                cleanup.execute(sa.text("DELETE FROM tenants WHERE code = 'rotest'"))
