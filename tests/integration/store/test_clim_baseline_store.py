from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.types.domain import ClimBaseline
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    import sqlalchemy as sa


def _seed_station(conn: sa.Connection) -> StationId:
    from sapphire_flow.store.station_store import PgStationStore

    station = make_station_config(
        station_id=StationId(uuid.uuid4()),
        code=f"CB-{uuid.uuid4().hex[:6]}",
        network="test",
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _make_baseline(
    station_id: StationId,
    parameter: str,
    day_of_year: int,
    rolling_mean: float = 10.0,
    rolling_std: float = 2.0,
    sample_count: int = 30,
) -> ClimBaseline:
    return ClimBaseline(
        station_id=station_id,
        parameter=parameter,
        day_of_year=day_of_year,
        rolling_mean=rolling_mean,
        rolling_std=rolling_std,
        sample_count=sample_count,
    )


class TestStoreAndFetchBaselines:
    def test_store_and_fetch_baselines(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgClimBaselineStore(db_connection)

        baselines = [
            _make_baseline(sid, "discharge", 1, rolling_mean=5.0),
            _make_baseline(sid, "discharge", 2, rolling_mean=6.0),
            _make_baseline(sid, "discharge", 3, rolling_mean=7.0),
        ]
        store.store_baselines(baselines)

        results = store.fetch_baselines(sid, "discharge")
        assert len(results) == 3
        assert [r.day_of_year for r in results] == [1, 2, 3]
        assert results[0].rolling_mean == pytest.approx(5.0)
        assert results[1].rolling_mean == pytest.approx(6.0)
        assert results[2].rolling_mean == pytest.approx(7.0)
        for r in results:
            assert r.station_id == sid
            assert r.parameter == "discharge"


class TestStoreUpsert:
    def test_store_upsert(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgClimBaselineStore(db_connection)

        original = _make_baseline(
            sid, "temperature", 100, rolling_mean=15.0, sample_count=30
        )
        store.store_baselines([original])

        updated = _make_baseline(
            sid, "temperature", 100, rolling_mean=16.5, rolling_std=3.0, sample_count=50
        )
        store.store_baselines([updated])

        results = store.fetch_baselines(sid, "temperature")
        assert len(results) == 1
        r = results[0]
        assert r.rolling_mean == pytest.approx(16.5)
        assert r.rolling_std == pytest.approx(3.0)
        assert r.sample_count == 50


class TestFetchSingleBaseline:
    def test_fetch_single_baseline(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgClimBaselineStore(db_connection)

        b = _make_baseline(
            sid,
            "precipitation",
            200,
            rolling_mean=3.2,
            rolling_std=1.1,
            sample_count=25,
        )
        store.store_baselines([b])

        result = store.fetch_baseline(sid, "precipitation", 200)
        assert result is not None
        assert result.station_id == sid
        assert result.parameter == "precipitation"
        assert result.day_of_year == 200
        assert result.rolling_mean == pytest.approx(3.2)
        assert result.rolling_std == pytest.approx(1.1)
        assert result.sample_count == 25


class TestFetchNonexistent:
    def test_fetch_baselines_empty(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgClimBaselineStore(db_connection)

        results = store.fetch_baselines(sid, "discharge")
        assert results == []

    def test_fetch_baseline_returns_none(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgClimBaselineStore(db_connection)

        result = store.fetch_baseline(sid, "discharge", 42)
        assert result is None
