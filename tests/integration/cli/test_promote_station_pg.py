from __future__ import annotations

import random
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from shapely.geometry import box

from sapphire_flow.cli import promote_station as cli
from sapphire_flow.services.station_promotion import (
    PromotionAction,
    PromotionPlan,
    apply_promotion,
)
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelAssignmentStatus,
    SpatialRepresentation,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import BasinId, ModelId, StationGroupId
from sapphire_flow.types.model import ModelRecord
from sapphire_flow.types.station import (
    GatewayPolygonBindingRow,
    GroupModelAssignment,
    StationGroup,
    StationWeatherSource,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_observation, make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sapphire_flow.types.auth import AuditEntry
    from sapphire_flow.types.station import StationConfig

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
PRINCIPAL = WritePrincipal(id=None, tenant_id=None)


class _FailingAudit:
    def append_entry(self, entry: AuditEntry) -> None:
        raise RuntimeError("audit insert failed")


def _stores(conn: sa.Connection) -> ReadinessStores:
    return ReadinessStores(
        stations=PgStationStore(conn),
        basins=PgBasinStore(conn),
        observations=PgObservationStore(conn),
        forcing=PgHistoricalForcingStore(conn),
        ratings=PgRatingCurveStore(conn),
        groups=PgStationGroupStore(conn),
        polygons=RecapGatewayPolygonStore(conn),
    )


def _seed(conn: sa.Connection) -> PromotionPlan:
    station = make_station_config(
        code="00001",
        network="research",
        station_status=StationStatus.ONBOARDING,
        forecast_targets=None,
    )
    PgStationStore(conn).store_station(station)
    return PromotionPlan(
        station=station,
        action=PromotionAction.PROMOTE,
        target="discharge",
        models=(),
        conditions=(),
        warnings=(),
    )


def _status(conn: sa.Connection) -> StationStatus:
    station = PgStationStore(conn).fetch_station_by_code("00001", "research")
    assert station is not None
    return station.station_status


class TestPromoteStationPg:
    def test_promotion_writes_status_targets_and_audit_together(
        self, db_connection: sa.Connection
    ) -> None:
        plan = _seed(db_connection)
        apply_promotion(
            plan,
            stores=_stores(db_connection),
            audit=PgAuditLogStore(db_connection),
            principal=PRINCIPAL,
            clock=lambda: NOW,
        )
        station = PgStationStore(db_connection).fetch_station_by_code(
            "00001", "research"
        )
        assert station is not None
        assert station.station_status is StationStatus.OPERATIONAL
        assert station.forecast_targets == frozenset({"discharge"})
        audited = db_connection.scalar(
            sa.text(
                "SELECT count(*) FROM audit_log WHERE target_id = :i "
                "AND event_type = 'station_status_change'"
            ),
            {"i": str(station.id)},
        )
        assert audited == 1
        operational = [
            s
            for s in PgStationStore(db_connection).fetch_all_stations()
            if s.station_status is StationStatus.OPERATIONAL
        ]
        assert [s.code for s in operational] == ["00001"]

    def test_audit_failure_rolls_the_status_back(
        self, db_connection: sa.Connection
    ) -> None:
        plan = _seed(db_connection)
        with pytest.raises(RuntimeError, match="audit insert failed"):  # noqa: SIM117
            with db_connection.begin_nested():
                apply_promotion(
                    plan,
                    stores=_stores(db_connection),
                    audit=_FailingAudit(),  # type: ignore[arg-type]
                    principal=PRINCIPAL,
                    clock=lambda: NOW,
                )
        assert _status(db_connection) is StationStatus.ONBOARDING


HRU = "hru1"
MODEL = ModelId("linear_regression_daily")


class _Engine:
    """Hands the test's rolled-back connection to main() in a savepoint."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    @contextmanager
    def begin(self) -> Iterator[sa.Connection]:
        with self._conn.begin_nested():
            yield self._conn


def _seed_ready(conn: sa.Connection, *, days: int) -> StationConfig:
    basin = Basin(
        id=BasinId(uuid4()),
        code="b1",
        name="B1",
        geometry=box(0, 0, 1, 1),
        area_km2=1.0,
        attributes={},
        band_geometries=None,
        created_at=NOW,
        network="research",
    )
    PgBasinStore(conn).store_basin(basin)
    station = make_station_config(
        code="00001",
        network="research",
        tz="UTC",
        basin_id=basin.id,
        station_status=StationStatus.ONBOARDING,
        forecast_targets=None,
        measured_parameters=frozenset({"discharge"}),
    )
    stations = PgStationStore(conn)
    stations.store_station(station)
    for role in WeatherSourceRole:
        stations.store_weather_source(
            StationWeatherSource(
                station_id=station.id,
                nwp_source=f"src_{role.value}",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=role,
            )
        )
    RecapGatewayPolygonStore(conn).store_binding(
        GatewayPolygonBindingRow(
            station_id=station.id,
            basin_id=basin.id,
            gateway_hru_name=HRU,
            name=HRU,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
        )
    )
    PgModelStore(conn).register_model(
        ModelRecord(
            id=MODEL,
            display_name="lr",
            artifact_scope=ArtifactScope.GROUP,
            description="",
            created_at=NOW,
        )
    )
    groups = PgStationGroupStore(conn, transaction_factory=lambda: nullcontext(conn))
    group = StationGroup(
        id=StationGroupId(uuid4()),
        name="g1",
        station_ids=frozenset({station.id}),
        created_at=NOW,
        tenant_id=station.tenant_id,
    )
    groups.store_group(group)
    groups.store_group_model_assignment(
        GroupModelAssignment(
            group_id=group.id,
            model_id=MODEL,
            time_step=timedelta(hours=24),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=NOW,
        )
    )
    now = datetime.now(UTC)
    rng = random.Random(1)
    PgObservationStore(conn).store_observations(
        [
            make_observation(
                station_id=station.id,
                value=1.0 + i,
                timestamp=ensure_utc(now - timedelta(days=i + 1)),
                rng=rng,
            )
            for i in range(days)
        ]
    )
    return station


def _main(
    conn: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *extra: str,
) -> int:
    config = tmp_path / "config.toml"
    config.write_text(
        '[deployment]\nwritable_tenants = ["sapphire"]\npromotion_allowed = true\n'
    )
    monkeypatch.setenv("SAPPHIRE_CONFIG", str(config))
    monkeypatch.setattr(cli, "create_engine_from_env", lambda: _Engine(conn))
    monkeypatch.setattr(cli, "resolve_scope", lambda *_a: (HRU, HRU))
    return cli.main(
        ["--tenant", "sapphire", "--network", "research", "--code", "00001",
         "--operator", "bea", "--weather-scope", "s", *extra]
    )  # fmt: skip


def _audit_rows(conn: sa.Connection) -> int:
    return int(
        conn.scalar(
            sa.text(
                "SELECT count(*) FROM audit_log "
                "WHERE event_type = 'station_status_change'"
            )
        )
        or 0
    )


class TestMainAgainstPostgres:
    def test_ready_station_is_promoted_through_main(
        self,
        db_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_ready(db_connection, days=7)
        assert _main(db_connection, monkeypatch, tmp_path) == 0
        assert _status(db_connection) is StationStatus.ONBOARDING
        assert _audit_rows(db_connection) == 0
        assert _main(db_connection, monkeypatch, tmp_path, "--apply") == 0
        assert _status(db_connection) is StationStatus.OPERATIONAL
        assert _audit_rows(db_connection) == 1

    def test_gap_in_the_lookback_refuses_and_writes_nothing(
        self,
        db_connection: sa.Connection,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _seed_ready(db_connection, days=6)
        assert _main(db_connection, monkeypatch, tmp_path, "--apply") == 3
        assert _status(db_connection) is StationStatus.ONBOARDING
        assert _audit_rows(db_connection) == 0
