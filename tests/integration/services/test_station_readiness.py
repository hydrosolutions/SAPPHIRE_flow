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
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import TenantId
from tests.conftest import make_observation

if TYPE_CHECKING:
    import pytest

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
                    timestamp=ensure_utc(NOW - timedelta(days=i)),
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
