from __future__ import annotations

import random
import zlib
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.api.publication_gate import PublicationGate
from sapphire_flow.api.routes.api_forecasts import get_forecast
from sapphire_flow.api.security import Principal
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.forecast_evidence import (
    capture_group_evidence,
    capture_station_evidence,
)
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId, StationGroupId
from sapphire_flow.types.model import GroupModelInputs
from tests.integration.api.test_dashboard_forecasts import (
    _client,
    app_overrides_clear,
)
from tests.integration.api.test_dashboard_forecasts import (
    _reset_reflected as _reset_reflected,
)
from tests.integration.store.test_forecast_store import savepoint_factory
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _station_model_inputs,
)

if TYPE_CHECKING:
    import sqlalchemy as sa


@pytest.mark.parametrize("kind", ["station", "group"])
@pytest.mark.parametrize("purpose", ["standard", "provisional_rating"])
def test_new_legacy_capture_over_selected_ceiling_retains_existing_reads(
    db_connection: sa.Connection,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    purpose: str,
) -> None:
    ordinary, provisional = native_pair(db_connection)
    forecast = ordinary if purpose == "standard" else provisional
    config = DeploymentConfig(max_retention_days=1000)
    rules = ForecastQcRuleSet(version="fixture", rules=())
    common = dict(
        model=object(),
        model_id=forecast.model_id,
        artifact_bytes=b"legacy-weights",
        rng_state=random.Random(3).getstate(),
        config=config,
        qc_rules=rules,
        qc_overrides=[],
    )
    if kind == "station":
        inputs = replace(
            _station_model_inputs(),
            station_id=forecast.station_id,
            issue_time=forecast.issued_at,
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            prior_state=b"x" * (25 * 1024 * 1024),
            baselines=[],
            water_level_datum_masl=None,
            **common,
        )
    else:
        sid = str(forecast.station_id)
        targets = pl.DataFrame(
            {
                "station_id": [sid],
                "timestamp": [forecast.issued_at - timedelta(days=1)],
                "discharge": [1.0],
            }
        )
        weather = pl.DataFrame(
            {
                "station_id": [sid],
                "timestamp": [forecast.issued_at],
                "precipitation": [1.0],
                "temperature": [2.0],
            }
        )
        inputs = GroupModelInputs(
            group_id=StationGroupId(uuid4()),
            station_ids=(forecast.station_id,),
            past_targets=targets,
            past_dynamic=weather,
            future_dynamic=weather,
            static=pl.DataFrame(
                {"station_id": [sid], "description": ["x" * (25 * 1024 * 1024)]}
            ),
            issue_time=forecast.issued_at,
            forecast_horizon_steps=1,
            time_step=timedelta(days=1),
        )
        evidence = capture_group_evidence(
            inputs=inputs, baselines_by_station={}, water_level_datums_masl={}, **common
        )
    assert evidence.weather_evidence_version is None and evidence.snapshot is not None
    assert len(zlib.decompress(evidence.snapshot)) > 32 * 1024 * 1024
    store = PgForecastStore(
        db_connection,
        data_use=forecast.data_use,
        transaction_factory=savepoint_factory(db_connection),
    )
    store.store_forecast(replace(forecast, evidence=evidence))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("legacy reads must not add weather evidence reads or decompression")

    monkeypatch.setattr(PgForecastStore, "fetch_evidence", forbidden)
    monkeypatch.setattr(
        "sapphire_flow.services.selected_weather_evidence.restore_bounded_weather_snapshot",
        forbidden,
    )
    monkeypatch.setattr(
        "sapphire_flow.services.forecast_evidence.restore_snapshot", forbidden
    )
    if purpose == "standard":
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        detail = get_forecast(
            str(forecast.id), {"forecast_store": store}, principal, PublicationGate()
        )
        assert detail.weather_use is None
    client = _client(db_connection)
    try:
        response = client.get(
            f"/api/v1/forecasts/{forecast.id}/data.json?data_use={purpose}"
        )
        html = client.get(f"/forecasts/{forecast.id}/?data_use={purpose}")
    finally:
        app_overrides_clear()
    assert response.status_code == 200 and response.json()["weather_use"] is None
    assert html.status_code == 200
    if purpose == "provisional_rating":
        assert response.json()["rating_input"] and "PROVISIONAL" in html.text
