from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection, get_connection_rw, get_stores
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AlertStatus
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from uuid import UUID

    from sapphire_flow.types.ids import AlertId


class _DummyConnection:
    def execute(self, *a: object, **kw: object) -> None:
        pass


class AckAwareFakeAlertStore(FakeAlertStore):
    def acknowledge_alert(self, alert_id: AlertId, acknowledged_by: UUID) -> None:
        a = self._alerts[alert_id]
        now = ensure_utc(datetime.now(UTC))
        self._alerts[alert_id] = replace(
            a,
            status=AlertStatus.ACKNOWLEDGED,
            acknowledged_by=acknowledged_by,
            acknowledged_at=now,
        )


@pytest.fixture
def fake_stores() -> dict[str, Any]:
    return {
        "station_store": FakeStationStore(),
        "obs_store": FakeObservationStore(),
        "forecast_store": FakeForecastStore(),
        "forcing_store": FakeHistoricalForcingStore(),
        "alert_store": AckAwareFakeAlertStore(),
        "artifact_store": FakeModelArtifactStore(),
        "pipeline_health_store": FakePipelineHealthStore(),
    }


@pytest.fixture
def client(fake_stores: dict[str, Any]) -> Generator[TestClient, None, None]:
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    app.dependency_overrides[get_stores] = lambda: fake_stores
    app.dependency_overrides[get_connection] = lambda: _DummyConnection()
    app.dependency_overrides[get_connection_rw] = lambda: _DummyConnection()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
