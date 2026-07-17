"""Plan 115b4 §6D — the dashboard forcing endpoint serves HYBRID-RESOLVED
rows, with the winning ``source`` tag per point, instead of the raw
un-prioritized merge of every provenance stream for a station.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_raw_historical_forcing, make_station_config

if TYPE_CHECKING:
    from typing import Any

    from fastapi.testclient import TestClient

_DAY = datetime(2026, 5, 1, tzinfo=UTC)


class TestStationForcingJson:
    def test_returns_only_the_hybrid_winning_row_per_point_with_source_tag(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(code="2135", name="Aare Bern")
        fake_stores["station_store"].store_station(station)
        fake_stores["station_store"].store_weather_source(
            StationWeatherSource(
                station_id=station.id,
                nwp_source="meteoswiss_open_data_reanalysis",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        # Both RhiresD and RprelimD cover the SAME point — the endpoint must
        # serve only the RhiresD (definitive) winner, not both.
        fake_stores["forcing_store"].store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.METEOSWISS_RHIRESD.value,
                    version="v1",
                    valid_time=ensure_utc(_DAY),
                    parameter="precipitation",
                    value=6.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.METEOSWISS_RPRELIMD.value,
                    version="v1",
                    valid_time=ensure_utc(_DAY),
                    parameter="precipitation",
                    value=999.0,
                ),
            ]
        )

        resp = client.get(
            f"/api/v1/stations/{station.id}/forcing.json",
            params={
                "start": "2026-04-01T00:00:00+00:00",
                "end": "2026-06-01T00:00:00+00:00",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        precip = body["series"]["precipitation"]
        assert len(precip["values"]) == 1
        assert precip["values"][0] == 6.0
        assert precip["sources"][0] == "meteoswiss_rhiresd"

    def test_no_reanalysis_binding_yields_empty_series(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(code="2136", name="No Binding")
        fake_stores["station_store"].store_station(station)

        resp = client.get(
            f"/api/v1/stations/{station.id}/forcing.json",
            params={
                "start": "2026-04-01T00:00:00+00:00",
                "end": "2026-06-01T00:00:00+00:00",
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"series": {}}

    def test_malformed_station_id_returns_400_not_500(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        # A non-UUID station_id must not crash the route with a 500 from an
        # unguarded UUID(...) ValueError — it is a client error (400).
        resp = client.get(
            "/api/v1/stations/not-a-uuid/forcing.json",
            params={
                "start": "2026-04-01T00:00:00+00:00",
                "end": "2026-06-01T00:00:00+00:00",
            },
        )

        assert resp.status_code == 400
