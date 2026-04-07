from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.historical_forcing import (
    HistoricalForcingRecord,
    RawHistoricalForcing,
)
from sapphire_flow.types.ids import HistoricalForcingId, StationId
from sapphire_flow.types.station import StationWeatherSource
from tests.fakes.fake_stores import FakeHistoricalForcingStore


def _make_record(
    station_id: str = "s1",
    parameter: str = "precipitation",
    hour: int = 0,
) -> HistoricalForcingRecord:
    return HistoricalForcingRecord(
        id=HistoricalForcingId(f"hf-{station_id}-{parameter}-{hour}"),
        station_id=StationId(station_id),
        source="smn",
        version="v1",
        valid_time=ensure_utc(datetime(2024, 1, 1, hour, tzinfo=UTC)),
        parameter=parameter,
        spatial_type=SpatialRepresentation.POINT,
        band_id=None,
        member_id=None,
        value=float(hour),
        created_at=ensure_utc(datetime(2024, 6, 1, tzinfo=UTC)),
    )


def _make_weather_source(station_id: str = "s1") -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(station_id),
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
    )


class TestStoreBackedReanalysisSource:
    def test_returns_raw_forcing_from_store(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _make_record(parameter="precipitation", hour=0),
                _make_record(parameter="temperature", hour=0),
            ]
        )
        adapter = StoreBackedReanalysisSource(store)

        start = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2024, 1, 2, tzinfo=UTC))
        result = adapter.fetch_reanalysis(
            station_configs=[_make_weather_source()],
            start=start,
            end=end,
            parameters=["precipitation", "temperature"],
        )

        assert len(result) == 2
        assert all(isinstance(r, RawHistoricalForcing) for r in result)
        params = {r.parameter for r in result}
        assert params == {"precipitation", "temperature"}

    def test_filters_by_parameters(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _make_record(parameter="precipitation", hour=0),
                _make_record(parameter="temperature", hour=0),
                _make_record(parameter="wind_speed", hour=0),
            ]
        )
        adapter = StoreBackedReanalysisSource(store)

        start = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2024, 1, 2, tzinfo=UTC))
        result = adapter.fetch_reanalysis(
            station_configs=[_make_weather_source()],
            start=start,
            end=end,
            parameters=["precipitation"],
        )

        assert len(result) == 1
        assert result[0].parameter == "precipitation"

    def test_multiple_stations(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _make_record(station_id="s1", parameter="precipitation", hour=0),
                _make_record(station_id="s2", parameter="precipitation", hour=0),
            ]
        )
        adapter = StoreBackedReanalysisSource(store)

        start = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2024, 1, 2, tzinfo=UTC))
        result = adapter.fetch_reanalysis(
            station_configs=[_make_weather_source("s1"), _make_weather_source("s2")],
            start=start,
            end=end,
            parameters=["precipitation"],
        )

        assert len(result) == 2
        station_ids = {r.station_id for r in result}
        assert station_ids == {StationId("s1"), StationId("s2")}

    def test_empty_store_returns_empty(self) -> None:
        store = FakeHistoricalForcingStore()
        adapter = StoreBackedReanalysisSource(store)

        start = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2024, 1, 2, tzinfo=UTC))
        result = adapter.fetch_reanalysis(
            station_configs=[_make_weather_source()],
            start=start,
            end=end,
            parameters=["precipitation"],
        )

        assert result == []
