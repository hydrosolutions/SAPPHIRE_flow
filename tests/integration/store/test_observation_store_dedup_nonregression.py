"""Non-regression guard: distinct natural keys still produce distinct rows.

Milestone: obs-ingest-upsert-cadence (acceptance criterion 6).

This is NOT a locked must-be-red test — distinct-key inserts have always
worked. It lives in a separate non-regression file so the upsert refactor
(``on_conflict_do_nothing`` -> predicate-scoped ``on_conflict_do_update`` plus
in-Python last-wins dedup) does not silently collapse readings at distinct
measurementTimes.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import StationId

from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config


def _seed_station(conn: sa.Connection, rng_seed: int, code: str) -> StationId:
    station = make_station_config(code=code, rng=random.Random(rng_seed))
    PgStationStore(conn).store_station(station)
    return station.id


def _utc(hour: int):
    return ensure_utc(datetime(2025, 1, 1, hour, tzinfo=UTC))


def _raw(station_id: StationId, hour: int, value: float) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=_utc(hour=hour),
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
    )


class TestDistinctTimestampsNonRegression:
    def test_distinct_timestamps_still_produce_distinct_rows(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=24, code="DEDUP-NR-001")
        store = PgObservationStore(db_connection)

        ids = store.store_raw_observations(
            [
                _raw(sid, hour=0, value=10.0),
                _raw(sid, hour=1, value=11.0),
            ]
        )
        assert len(ids) == 2

        more = store.store_raw_observations([_raw(sid, hour=2, value=12.0)])
        assert len(more) == 1

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=3),
        )
        assert len(fetched) == 3
        assert {o.value for o in fetched} == {10.0, 11.0, 12.0}
