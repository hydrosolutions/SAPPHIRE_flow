from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.protocols.stores import HistoricalForcingStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.historical_forcing import (
    HistoricalForcingRecord,
    RawHistoricalForcing,
)
from sapphire_flow.types.ids import HistoricalForcingId, StationId
from tests.conftest import make_historical_forcing_record
from tests.fakes.fake_stores import FakeHistoricalForcingStore

_NOW = ensure_utc(datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
_STATION = StationId(uuid4())


class TestRawHistoricalForcing:
    def test_valid_basin_average(self) -> None:
        r = RawHistoricalForcing(
            station_id=_STATION,
            source="camels-ch",
            version="1.0",
            valid_time=_NOW,
            parameter="precipitation",
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=None,
            value=5.2,
        )
        assert r.source == "camels-ch"
        assert r.band_id is None

    def test_valid_elevation_band(self) -> None:
        r = RawHistoricalForcing(
            station_id=_STATION,
            source="era5-land",
            version="2.0",
            valid_time=_NOW,
            parameter="temperature",
            spatial_type=SpatialRepresentation.ELEVATION_BAND,
            band_id=3,
            member_id=None,
            value=12.1,
        )
        assert r.band_id == 3

    def test_valid_point(self) -> None:
        r = RawHistoricalForcing(
            station_id=_STATION,
            source="smn",
            version="1.0",
            valid_time=_NOW,
            parameter="temperature",
            spatial_type=SpatialRepresentation.POINT,
            band_id=None,
            member_id=None,
            value=8.0,
        )
        assert r.spatial_type == SpatialRepresentation.POINT

    def test_band_id_required_for_elevation_band(self) -> None:
        with pytest.raises(ValueError, match="band_id is required"):
            RawHistoricalForcing(
                station_id=_STATION,
                source="era5-land",
                version="1.0",
                valid_time=_NOW,
                parameter="temperature",
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=None,
                member_id=None,
                value=10.0,
            )

    def test_band_id_forbidden_for_non_elevation_band(self) -> None:
        with pytest.raises(ValueError, match="band_id must be None"):
            RawHistoricalForcing(
                station_id=_STATION,
                source="camels-ch",
                version="1.0",
                valid_time=_NOW,
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=1,
                member_id=None,
                value=5.0,
            )

    def test_ensemble_member(self) -> None:
        r = RawHistoricalForcing(
            station_id=_STATION,
            source="era5",
            version="1.0",
            valid_time=_NOW,
            parameter="precipitation",
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=3,
            value=4.0,
        )
        assert r.member_id == 3


class TestHistoricalForcingRecord:
    def test_valid_construction(self) -> None:
        rec = HistoricalForcingRecord(
            id=HistoricalForcingId(uuid4()),
            station_id=_STATION,
            source="camels-ch",
            version="1.0",
            valid_time=_NOW,
            parameter="precipitation",
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=None,
            value=5.2,
            created_at=_NOW,
        )
        assert rec.source == "camels-ch"

    def test_band_id_required_for_elevation_band(self) -> None:
        with pytest.raises(ValueError, match="band_id is required"):
            HistoricalForcingRecord(
                id=HistoricalForcingId(uuid4()),
                station_id=_STATION,
                source="era5-land",
                version="1.0",
                valid_time=_NOW,
                parameter="temperature",
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=None,
                member_id=None,
                value=10.0,
                created_at=_NOW,
            )

    def test_band_id_forbidden_for_non_elevation_band(self) -> None:
        with pytest.raises(ValueError, match="band_id must be None"):
            HistoricalForcingRecord(
                id=HistoricalForcingId(uuid4()),
                station_id=_STATION,
                source="camels-ch",
                version="1.0",
                valid_time=_NOW,
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=2,
                member_id=None,
                value=5.0,
                created_at=_NOW,
            )


class TestFakeHistoricalForcingStore:
    def test_conformance(self) -> None:
        assert isinstance(FakeHistoricalForcingStore(), HistoricalForcingStore)

    def test_store_and_fetch_round_trip(self) -> None:
        store = FakeHistoricalForcingStore()
        rec = make_historical_forcing_record(station_id=_STATION)
        store.store_forcing([rec])
        result = store.fetch_forcing(
            _STATION, "camels-ch", _NOW - timedelta(hours=1), _NOW + timedelta(hours=1)
        )
        assert len(result) == 1
        assert result[0].id == rec.id

    def test_filter_by_parameters(self) -> None:
        store = FakeHistoricalForcingStore()
        r1 = make_historical_forcing_record(
            station_id=_STATION, parameter="precipitation"
        )
        r2 = make_historical_forcing_record(
            station_id=_STATION, parameter="temperature"
        )
        store.store_forcing([r1, r2])
        result = store.fetch_forcing(
            _STATION,
            "camels-ch",
            _NOW - timedelta(hours=1),
            _NOW + timedelta(hours=1),
            parameters=["temperature"],
        )
        assert len(result) == 1
        assert result[0].parameter == "temperature"

    def test_filter_by_version(self) -> None:
        store = FakeHistoricalForcingStore()
        r1 = make_historical_forcing_record(station_id=_STATION, version="1.0")
        r2 = make_historical_forcing_record(station_id=_STATION, version="2.0")
        store.store_forcing([r1, r2])
        result = store.fetch_forcing(
            _STATION,
            "camels-ch",
            _NOW - timedelta(hours=1),
            _NOW + timedelta(hours=1),
            version="2.0",
        )
        assert len(result) == 1
        assert result[0].version == "2.0"

    def test_filter_by_member_id(self) -> None:
        store = FakeHistoricalForcingStore()
        r1 = make_historical_forcing_record(station_id=_STATION, member_id=None)
        r2 = make_historical_forcing_record(station_id=_STATION, member_id=3)
        store.store_forcing([r1, r2])
        result = store.fetch_forcing(
            _STATION,
            "camels-ch",
            _NOW - timedelta(hours=1),
            _NOW + timedelta(hours=1),
            member_id=3,
        )
        assert len(result) == 1
        assert result[0].member_id == 3

    def test_fetch_forcing_as_dataframe_returns_pivoted(self) -> None:
        store = FakeHistoricalForcingStore()
        t1 = _NOW
        t2 = ensure_utc(datetime(2026, 1, 15, 13, 0, tzinfo=UTC))
        store.store_forcing(
            [
                make_historical_forcing_record(
                    station_id=_STATION,
                    valid_time=t1,
                    parameter="precipitation",
                    value=5.0,
                ),
                make_historical_forcing_record(
                    station_id=_STATION,
                    valid_time=t1,
                    parameter="temperature",
                    value=10.0,
                ),
                make_historical_forcing_record(
                    station_id=_STATION,
                    valid_time=t2,
                    parameter="precipitation",
                    value=3.0,
                ),
                make_historical_forcing_record(
                    station_id=_STATION,
                    valid_time=t2,
                    parameter="temperature",
                    value=12.0,
                ),
            ]
        )
        df = store.fetch_forcing_as_dataframe(
            _STATION,
            "camels-ch",
            _NOW - timedelta(hours=1),
            t2 + timedelta(hours=1),
        )
        assert df is not None
        assert df.shape == (2, 3)  # 2 rows, 3 cols (valid_time + 2 params)
        assert "precipitation" in df.columns
        assert "temperature" in df.columns

    def test_fetch_forcing_as_dataframe_returns_none_when_empty(self) -> None:
        store = FakeHistoricalForcingStore()
        df = store.fetch_forcing_as_dataframe(
            _STATION,
            "camels-ch",
            _NOW - timedelta(hours=1),
            _NOW + timedelta(hours=1),
        )
        assert df is None

    def test_fetch_available_sources(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                make_historical_forcing_record(station_id=_STATION, source="camels-ch"),
                make_historical_forcing_record(station_id=_STATION, source="era5"),
                make_historical_forcing_record(station_id=_STATION, source="camels-ch"),
            ]
        )
        sources = store.fetch_available_sources(_STATION)
        assert sources == ["camels-ch", "era5"]
