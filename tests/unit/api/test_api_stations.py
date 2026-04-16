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
    StationKind,
    StationStatus,
)
from sapphire_flow.types.ids import (
    ForecastId,
    ModelId,
    StationId,
)
from tests.conftest import (
    make_forecast_ensemble,
    make_observation,
    make_station_config,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _make_operational_forecast(
    *,
    station_id: StationId,
    issued_at: UtcDatetime | None = None,
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
        model_artifact_id=None,
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


class TestListStations:
    def test_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/stations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_returns_station(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fake_stores["station_store"].store_station(station)

        resp = client.get("/api/v1/stations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == str(station.id)
        assert item["code"] == station.code
        assert item["name"] == station.name
        assert item["station_kind"] == station.station_kind.value
        assert item["station_status"] == station.station_status.value
        assert item["network"] == station.network
        assert item["ownership"] == station.ownership.value
        assert isinstance(item["measured_parameters"], list)
        loc = item["location"]
        assert loc["lon"] == station.location.lon
        assert loc["lat"] == station.location.lat

    def test_pagination_limit(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        for i in range(3):
            s = make_station_config(code=f"S-{i}", rng=random.Random(i + 10))
            fake_stores["station_store"].store_station(s)

        resp = client.get("/api/v1/stations", params={"limit": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["total"] == 3
        assert body["limit"] == 1

    def test_pagination_offset(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        for i in range(3):
            s = make_station_config(code=f"S-{i}", rng=random.Random(i + 10))
            fake_stores["station_store"].store_station(s)

        resp = client.get("/api/v1/stations", params={"limit": 10, "offset": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["offset"] == 2

    def test_filter_by_kind(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        river = make_station_config(
            station_kind=StationKind.RIVER, rng=random.Random(1)
        )
        lake = make_station_config(
            station_kind=StationKind.LAKE, code="LAKE-1", rng=random.Random(2)
        )
        fake_stores["station_store"].store_station(river)
        fake_stores["station_store"].store_station(lake)

        resp = client.get("/api/v1/stations", params={"kind": "lake"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["station_kind"] == "lake"

    def test_filter_by_status(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        active = make_station_config(
            station_status=StationStatus.OPERATIONAL, rng=random.Random(1)
        )
        inactive = make_station_config(
            station_status=StationStatus.DECOMMISSIONED,
            code="DECOM-1",
            rng=random.Random(2),
        )
        fake_stores["station_store"].store_station(active)
        fake_stores["station_store"].store_station(inactive)

        resp = client.get("/api/v1/stations", params={"status": "operational"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["station_status"] == "operational"


class TestGetStation:
    def test_found(self, client: TestClient, fake_stores: dict[str, Any]) -> None:
        station = make_station_config(rng=random.Random(1))
        fake_stores["station_store"].store_station(station)

        resp = client.get(f"/api/v1/stations/{station.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(station.id)
        assert body["code"] == station.code
        assert body["timezone"] == station.timezone
        assert body["gauging_status"] == station.gauging_status.value
        assert "thresholds" in body
        assert "model_assignments" in body
        assert "weather_sources" in body
        assert "created_at" in body
        assert "updated_at" in body

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/stations/{uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body


class TestListObservations:
    def test_returns_observations(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        rng = random.Random(1)
        station = make_station_config(rng=rng)
        fake_stores["station_store"].store_station(station)

        obs = make_observation(
            station_id=station.id,
            parameter="discharge",
            timestamp=ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC)),
            rng=random.Random(2),
        )
        fake_stores["obs_store"].store_observations([obs])

        resp = client.get(
            f"/api/v1/stations/{station.id}/observations",
            params={
                "parameter": "discharge",
                "start": "2025-01-01T00:00:00Z",
                "end": "2025-01-02T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        item = body[0]
        assert item["id"] == str(obs.id)
        assert item["station_id"] == str(station.id)
        assert item["parameter"] == "discharge"
        assert item["source"] == obs.source.value
        assert item["qc_status"] == obs.qc_status.value
        assert isinstance(item["qc_flags"], list)

    def test_missing_required_params(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/stations/{uuid4()}/observations")
        assert resp.status_code == 422

    def test_empty_range(self, client: TestClient, fake_stores: dict[str, Any]) -> None:
        station = make_station_config(rng=random.Random(1))
        fake_stores["station_store"].store_station(station)

        resp = client.get(
            f"/api/v1/stations/{station.id}/observations",
            params={
                "parameter": "discharge",
                "start": "2025-06-01T00:00:00Z",
                "end": "2025-06-02T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == []


class TestListForecasts:
    def test_returns_forecasts(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fake_stores["station_store"].store_station(station)

        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(
            f"/api/v1/stations/{station.id}/forecasts",
            params={
                "start": "2024-12-31T00:00:00Z",
                "end": "2025-01-02T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == str(fc.id)
        assert item["station_id"] == str(station.id)
        assert item["model_id"] == str(fc.model_id)
        assert item["parameter"] == fc.ensemble.parameter
        assert item["representation"] == fc.representation.value
        assert item["status"] == fc.status.value
        assert item["nwp_cycle_source"] == fc.nwp_cycle_source.value

    def test_pagination(self, client: TestClient, fake_stores: dict[str, Any]) -> None:
        station = make_station_config(rng=random.Random(1))
        fake_stores["station_store"].store_station(station)

        for i in range(3):
            iat = ensure_utc(datetime(2025, 1, 1, i, tzinfo=UTC))
            fc = _make_operational_forecast(
                station_id=station.id, issued_at=iat, rng=random.Random(10 + i)
            )
            fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(
            f"/api/v1/stations/{station.id}/forecasts",
            params={
                "start": "2024-12-31T00:00:00Z",
                "end": "2025-01-02T00:00:00Z",
                "limit": 2,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3
        assert body["limit"] == 2

    def test_empty_when_no_forecasts(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fake_stores["station_store"].store_station(station)

        resp = client.get(
            f"/api/v1/stations/{station.id}/forecasts",
            params={
                "start": "2025-06-01T00:00:00Z",
                "end": "2025-06-02T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
