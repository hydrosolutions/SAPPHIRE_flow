from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import StationId
from tests.conftest import make_raw_historical_forcing, make_station_config

if TYPE_CHECKING:
    import sqlalchemy as sa


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    from sapphire_flow.store.station_store import PgStationStore

    station = make_station_config(
        station_id=StationId(uuid.uuid4()),
        code=f"HF-{uuid.uuid4().hex[:6]}",
        network="camels",
    )
    PgStationStore(conn).store_station(station)
    return station.id


class TestStoreAndFetch:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        raw = make_raw_historical_forcing(
            station_id=sid,
            source="camels-ch",
            version="1.0",
            valid_time=_utc(2026, 1, 10, 6),
            parameter="precipitation",
            value=3.5,
        )
        store.store_forcing([raw])

        records = store.fetch_forcing(
            sid,
            "camels-ch",
            _utc(2026, 1, 10),
            _utc(2026, 1, 11),
        )
        assert len(records) == 1
        r = records[0]
        assert r.station_id == sid
        assert r.source == "camels-ch"
        assert r.version == "1.0"
        assert r.parameter == "precipitation"
        assert r.value == pytest.approx(3.5)
        assert r.spatial_type == SpatialRepresentation.BASIN_AVERAGE
        assert r.band_id is None
        assert r.member_id is None
        assert r.id is not None
        assert r.created_at is not None


class TestStoreDedup:
    def test_store_same_records_twice(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        raw = make_raw_historical_forcing(
            station_id=sid,
            source="camels-ch",
            version="1.0",
            valid_time=_utc(2026, 2, 1, 0),
            parameter="temperature",
            value=12.0,
        )
        store.store_forcing([raw])
        store.store_forcing([raw])

        records = store.fetch_forcing(
            sid,
            "camels-ch",
            _utc(2026, 2, 1),
            _utc(2026, 2, 2),
        )
        assert len(records) == 1


class TestFetchHalfOpenRange:
    def test_half_open_range_semantics(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        times = [_utc(2026, 3, 1, h) for h in range(4)]
        for t in times:
            store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        source="camels-ch",
                        version="1.0",
                        valid_time=t,
                        parameter="precipitation",
                        value=float(t.hour),
                    )
                ]
            )

        start = _utc(2026, 3, 1, 1)
        end = _utc(2026, 3, 1, 3)
        records = store.fetch_forcing(sid, "camels-ch", start, end)
        valid_times = {r.valid_time for r in records}

        assert _utc(2026, 3, 1, 0) not in valid_times
        assert _utc(2026, 3, 1, 1) in valid_times
        assert _utc(2026, 3, 1, 2) in valid_times
        assert _utc(2026, 3, 1, 3) not in valid_times


class TestFetchWithFilters:
    def test_filter_by_parameter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        for param, val in [("precipitation", 5.0), ("temperature", 20.0)]:
            store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        source="camels-ch",
                        version="1.0",
                        valid_time=_utc(2026, 4, 1, 0),
                        parameter=param,
                        value=val,
                    )
                ]
            )

        records = store.fetch_forcing(
            sid,
            "camels-ch",
            _utc(2026, 4, 1),
            _utc(2026, 4, 2),
            parameters=["precipitation"],
        )
        assert len(records) == 1
        assert records[0].parameter == "precipitation"

    def test_filter_by_version(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        for ver, val in [("1.0", 1.0), ("2.0", 2.0)]:
            store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        source="camels-ch",
                        version=ver,
                        valid_time=_utc(2026, 5, 1, 0),
                        parameter="precipitation",
                        value=val,
                    )
                ]
            )

        records = store.fetch_forcing(
            sid,
            "camels-ch",
            _utc(2026, 5, 1),
            _utc(2026, 5, 2),
            version="2.0",
        )
        assert len(records) == 1
        assert records[0].version == "2.0"
        assert records[0].value == pytest.approx(2.0)


class TestFetchAsDataframe:
    def test_pivot_columns_by_parameter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        vt = _utc(2026, 6, 1, 0)
        for param, val in [("precipitation", 4.0), ("temperature", 18.0)]:
            store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        source="camels-ch",
                        version="1.0",
                        valid_time=vt,
                        parameter=param,
                        value=val,
                    )
                ]
            )

        df = store.fetch_forcing_as_dataframe(
            sid,
            "camels-ch",
            _utc(2026, 6, 1),
            _utc(2026, 6, 2),
        )
        assert df is not None
        assert isinstance(df, pl.DataFrame)
        assert "valid_time" in df.columns
        assert "precipitation" in df.columns
        assert "temperature" in df.columns
        assert len(df) == 1
        assert df["precipitation"][0] == pytest.approx(4.0)
        assert df["temperature"][0] == pytest.approx(18.0)


class TestFetchAsDataframeEmpty:
    def test_returns_none_when_no_records(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        result = store.fetch_forcing_as_dataframe(
            sid,
            "camels-ch",
            _utc(2026, 7, 1),
            _utc(2026, 7, 2),
        )
        assert result is None


class TestFetchAvailableSources:
    def test_sorted_unique_sources(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        for src in ["smn", "camels-ch", "era5"]:
            store.store_forcing(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        source=src,
                        version="1.0",
                        valid_time=_utc(2026, 8, 1, 0),
                        parameter="precipitation",
                        value=1.0,
                    )
                ]
            )

        sources = store.fetch_available_sources(sid)
        assert sources == sorted(["smn", "camels-ch", "era5"])

    def test_empty_when_no_records(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgHistoricalForcingStore(db_connection)

        assert store.fetch_available_sources(sid) == []
