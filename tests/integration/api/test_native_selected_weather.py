from __future__ import annotations

import json
import zlib
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import sqlalchemy as sa

from sapphire_flow.api.publication_gate import PublicationGate
from sapphire_flow.api.routes.api_forecasts import get_forecast
from sapphire_flow.api.security import Principal
from sapphire_flow.db.metadata import models
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import AccessTokenRole, ModelArtifactStatus
from sapphire_flow.types.ids import AccessTokenId, StationGroupId
from sapphire_flow.types.station import StationGroup
from tests.conftest import make_station_config
from tests.integration.store.test_forecast_store import savepoint_factory
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401
)
from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


def test_native_21_station_group_persists_and_reads_authorized_weather(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    forecasts = _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        station_count=21,
        lookback_days=30,
    )
    assert forecasts is not None and len(forecasts) == 21
    first = forecasts[0]
    evidence = first.evidence
    assert (
        evidence is not None
        and evidence.snapshot is not None
        and evidence.artifact is not None
    )
    raw = zlib.decompress(evidence.snapshot)
    snapshot = json.loads(raw)
    record_property("decoded_snapshot_bytes", len(raw))
    record_property("compressed_snapshot_bytes", len(evidence.snapshot))
    assert 16 * 1024 * 1024 < len(raw) <= 32 * 1024 * 1024
    group_id = StationGroupId(UUID(snapshot["group_id"]))
    station_store = PgStationStore(db_connection)
    for forecast in forecasts:
        station_store.store_station(
            make_station_config(
                station_id=forecast.station_id,
                code=f"weather-{forecast.station_id.int}",
            )
        )
    PgStationGroupStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    ).store_group(
        StationGroup(
            id=group_id,
            name="native 21 weather fixture",
            station_ids=frozenset(forecast.station_id for forecast in forecasts),
            created_at=first.issued_at,
        )
    )
    db_connection.execute(
        sa.insert(models).values(
            id=first.model_id,
            display_name="Native weather fixture",
            artifact_scope="group",
            description="Offline fake model",
            created_at=first.issued_at,
        )
    )
    artifact = PgModelArtifactStore(db_connection, tmp_path).store_artifact(
        model_id=first.model_id,
        artifact_bytes=evidence.artifact,
        training_period_start=first.issued_at - timedelta(days=100),
        training_period_end=first.issued_at - timedelta(days=10),
        trained_at=first.issued_at,
        group_id=group_id,
        status=ModelArtifactStatus.ACTIVE,
    )
    store = PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    )
    for forecast in forecasts:
        store.store_forecast(replace(forecast, model_artifact_id=artifact.artifact_id))
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.CONSUMER,
        tenant_id=None,
        station_ids=frozenset({first.station_id}),
    )
    response = get_forecast(
        str(first.id), {"forecast_store": store}, principal, PublicationGate()
    )
    assert response.weather_use is not None
    assert response.weather_use.station_id == str(first.station_id)
    assert response.weather_use.past.freshness == "current"
    assert response.weather_use.future.freshness == "current"
    encoded = response.weather_use.model_dump_json()
    assert not any(str(forecast.station_id) in encoded for forecast in forecasts[1:])
