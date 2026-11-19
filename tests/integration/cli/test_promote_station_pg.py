from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from sapphire_flow.services.station_promotion import (
    PromotionAction,
    PromotionPlan,
    apply_promotion,
)
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import StationStatus
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.auth import AuditEntry

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
