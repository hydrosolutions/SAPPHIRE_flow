from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
)
from sapphire_flow.types.ids import (
    ArtifactId,
    ForecastId,
    ModelId,
    StationId,
)
from tests.conftest import make_forecast_ensemble, make_station_config

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _make_operational_forecast(
    *,
    station_id: StationId,
    issued_at: UtcDatetime | None = None,
    model_artifact_id: ArtifactId | None = None,
    rng: random.Random | None = None,
) -> Any:
    from sapphire_flow.types.forecast import OperationalForecast

    rng = rng or random.Random(99)
    iat = issued_at or _EPOCH
    ensemble = make_forecast_ensemble(
        station_id=station_id, rng=rng, n_members=3, n_steps=5
    )
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=ModelId("test_model"),
        model_artifact_id=model_artifact_id,
        issued_at=iat,
        nwp_cycle_reference_time=iat,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=EnsembleRepresentation.MEMBERS,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=iat,
        updated_at=iat,
    )


class TestGetForecast:
    def test_found(self, client: TestClient, fake_stores: dict[str, Any]) -> None:
        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(fc.id)
        assert body["station_id"] == str(station.id)
        assert body["model_id"] == str(fc.model_id)
        assert body["representation"] == fc.representation.value
        assert body["status"] == fc.status.value
        assert body["version"] == fc.version
        assert body["nwp_cycle_source"] == fc.nwp_cycle_source.value
        assert body["model_artifact_id"] is None
        assert body["warm_up_source"] is None
        assert body["combination_strategy"] is None
        assert body["source_model_ids"] is None

    def test_ensemble_shape(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        body = resp.json()
        ens = body["ensemble"]
        assert ens["representation"] == "members"
        assert ens["parameter"] == fc.ensemble.parameter
        assert ens["units"] == fc.ensemble.units
        assert ens["forecast_horizon_steps"] == fc.ensemble.forecast_horizon_steps
        assert ens["time_step_seconds"] == int(fc.ensemble.time_step.total_seconds())
        assert ens["member_count"] == fc.ensemble.member_count
        assert isinstance(ens["valid_times"], list)
        assert len(ens["valid_times"]) == fc.ensemble.forecast_horizon_steps
        assert isinstance(ens["series"], dict)
        assert len(ens["series"]) == fc.ensemble.member_count
        for _member_key, values in ens["series"].items():
            assert len(values) == fc.ensemble.forecast_horizon_steps

    def test_runoff_only_exposes_null_reference_time(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        # epic-088 M4: a runoff-only forecast serialises source "runoff_only"
        # and a NULL nwp_cycle_reference_time. RED on main: the schema pins
        # nwp_cycle_reference_time: datetime (non-optional) and NwpCycleSource
        # has no RUNOFF_ONLY, so a null reference triggers a 500.
        from sapphire_flow.types.forecast import OperationalForecast

        station = make_station_config(rng=random.Random(1))
        ensemble = make_forecast_ensemble(
            station_id=station.id, rng=random.Random(5), n_members=3, n_steps=5
        )
        fc = OperationalForecast(
            id=ForecastId(uuid4()),
            station_id=station.id,
            model_id=ModelId("runoff_only_model"),
            model_artifact_id=None,
            issued_at=_EPOCH,
            nwp_cycle_reference_time=None,  # type: ignore[arg-type]
            nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
            representation=EnsembleRepresentation.MEMBERS,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=None,
            warm_up_state_age_hours=None,
            observation_staleness_hours=None,
            ensemble=ensemble,
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nwp_cycle_source"] == "runoff_only"
        assert body["nwp_cycle_reference_time"] is None

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/forecasts/{uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body

    def test_with_artifact_id(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        artifact_id = ArtifactId(uuid4())
        fc = _make_operational_forecast(
            station_id=station.id,
            model_artifact_id=artifact_id,
            rng=random.Random(2),
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_artifact_id"] == str(artifact_id)

    def test_quantile_representation(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from sapphire_flow.types.forecast import OperationalForecast

        station = make_station_config(rng=random.Random(1))
        rng = random.Random(3)
        ensemble = make_forecast_ensemble(
            station_id=station.id,
            representation=EnsembleRepresentation.QUANTILES,
            rng=rng,
            n_steps=5,
        )
        fc = OperationalForecast(
            id=ForecastId(uuid4()),
            station_id=station.id,
            model_id=ModelId("quant_model"),
            model_artifact_id=None,
            issued_at=_EPOCH,
            nwp_cycle_reference_time=_EPOCH,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            representation=EnsembleRepresentation.QUANTILES,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=None,
            warm_up_state_age_hours=None,
            observation_staleness_hours=None,
            ensemble=ensemble,
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        ens = resp.json()["ensemble"]
        assert ens["representation"] == "quantiles"
        assert isinstance(ens["series"], dict)
        for _q_key, values in ens["series"].items():
            assert len(values) == 5
