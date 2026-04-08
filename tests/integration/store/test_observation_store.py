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
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_observation, make_station_config


def _seed_station(
    conn: sa.Connection, rng_seed: int = 1, code: str = "TEST-001"
) -> StationId:
    station = make_station_config(code=code, rng=random.Random(rng_seed))
    PgStationStore(conn).store_station(station)
    return station.id


def _utc(year: int = 2025, month: int = 1, day: int = 1, hour: int = 0):
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _raw(station_id: StationId, hour: int, value: float = 10.0) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=_utc(hour=hour),
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
    )


class TestStoreRawAndFetch:
    def test_store_raw_and_fetch(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgObservationStore(db_connection)

        raws = [_raw(sid, hour=0), _raw(sid, hour=1)]
        ids = store.store_raw_observations(raws)

        assert len(ids) == 2

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=2),
        )
        assert len(fetched) == 2
        assert all(o.qc_status == QcStatus.RAW for o in fetched)
        assert all(o.qc_flags == [] for o in fetched)
        assert all(o.station_id == sid for o in fetched)


class TestUpdateQc:
    def test_update_qc(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=2)
        store = PgObservationStore(db_connection)

        [oid] = store.store_raw_observations([_raw(sid, hour=0)])

        flag = QcFlag(
            rule_id="range_check",
            rule_version="1.0",
            status=QcStatus.QC_SUSPECT,
            detail="value near upper bound",
        )
        store.update_qc(oid, QcStatus.QC_SUSPECT, [flag], qc_rule_version="1.0")

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
        obs = fetched[0]
        assert obs.qc_status == QcStatus.QC_SUSPECT
        assert len(obs.qc_flags) == 1
        assert obs.qc_flags[0].rule_id == "range_check"
        assert obs.qc_flags[0].status == QcStatus.QC_SUSPECT
        assert obs.qc_rule_version == "1.0"


class TestFetchHalfOpenRange:
    def test_fetch_half_open_range(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=3)
        store = PgObservationStore(db_connection)

        store.store_raw_observations(
            [
                _raw(sid, hour=0),
                _raw(sid, hour=6),
                _raw(sid, hour=12),
                _raw(sid, hour=23),
            ]
        )

        start = _utc(hour=6)
        end = _utc(hour=12)
        fetched = store.fetch_observations(
            station_id=sid, parameter="discharge", start=start, end=end
        )

        timestamps = {o.timestamp for o in fetched}
        assert _utc(hour=6) in timestamps
        assert _utc(hour=12) not in timestamps
        assert _utc(hour=0) not in timestamps
        assert _utc(hour=23) not in timestamps


class TestFetchWithFilters:
    def test_filter_by_qc_status(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=4)
        store = PgObservationStore(db_connection)

        [oid0, _oid1] = store.store_raw_observations(
            [_raw(sid, hour=0), _raw(sid, hour=1)]
        )
        store.update_qc(oid0, QcStatus.QC_PASSED, [])

        passed = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=2),
            qc_status=QcStatus.QC_PASSED,
        )
        assert len(passed) == 1
        assert passed[0].id == oid0

    def test_filter_by_source(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=5)
        store = PgObservationStore(db_connection)

        # store one obs via store_raw_observations (source=MEASURED)
        store.store_raw_observations([_raw(sid, hour=0)])

        # store another with source=MANUAL_IMPORT via store_observations
        obs_manual = make_observation(
            station_id=sid,
            timestamp=_utc(hour=1),
            qc_status=QcStatus.QC_PASSED,
            rng=random.Random(5),
        )
        from dataclasses import replace

        obs_manual = replace(obs_manual, source=ObservationSource.MANUAL_IMPORT)
        store.store_observations([obs_manual])

        measured = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=2),
            source=ObservationSource.MEASURED,
        )
        assert len(measured) == 1
        assert measured[0].source == ObservationSource.MEASURED


class TestFetchBatch:
    def test_fetch_batch(self, db_connection: sa.Connection) -> None:
        sid1 = _seed_station(db_connection, rng_seed=6, code="BATCH-001")
        sid2 = _seed_station(db_connection, rng_seed=7, code="BATCH-002")
        store = PgObservationStore(db_connection)

        store.store_raw_observations([_raw(sid1, hour=0), _raw(sid1, hour=1)])
        store.store_raw_observations([_raw(sid2, hour=0)])

        result = store.fetch_observations_batch(
            station_ids=[sid1, sid2],
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=2),
        )

        assert set(result.keys()) == {sid1, sid2}
        assert len(result[sid1]) == 2
        assert len(result[sid2]) == 1
        assert all(o.station_id == sid1 for o in result[sid1])
        assert all(o.station_id == sid2 for o in result[sid2])

    def test_fetch_batch_empty_station_ids(self, db_connection: sa.Connection) -> None:
        store = PgObservationStore(db_connection)
        result = store.fetch_observations_batch(
            station_ids=[], parameter="discharge", start=_utc(hour=0), end=_utc(hour=1)
        )
        assert result == {}


class TestFetchLatestTimestamp:
    def test_fetch_latest_timestamp(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=8)
        store = PgObservationStore(db_connection)

        store.store_raw_observations(
            [_raw(sid, hour=0), _raw(sid, hour=6), _raw(sid, hour=12)]
        )

        latest = store.fetch_latest_timestamp(sid, "discharge")
        assert latest is not None
        assert latest == _utc(hour=12)

    def test_fetch_latest_timestamp_none_when_empty(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=9)
        store = PgObservationStore(db_connection)

        result = store.fetch_latest_timestamp(sid, "discharge")
        assert result is None


class TestStoreObservationsUpsert:
    def test_upsert_does_not_duplicate(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=10)
        store = PgObservationStore(db_connection)

        obs = make_observation(
            station_id=sid,
            timestamp=_utc(hour=0),
            qc_status=QcStatus.QC_PASSED,
            value=42.0,
            rng=random.Random(10),
        )
        store.store_observations([obs])

        # Same natural key, different value
        from dataclasses import replace

        obs_updated = replace(obs, value=99.0)
        store.store_observations([obs_updated])

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
        assert fetched[0].value == 99.0

    def test_upsert_updates_qc_fields(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, rng_seed=11)
        store = PgObservationStore(db_connection)

        obs = make_observation(
            station_id=sid,
            timestamp=_utc(hour=0),
            qc_status=QcStatus.RAW,
            value=10.0,
            rng=random.Random(11),
        )
        store.store_observations([obs])

        from dataclasses import replace

        obs_qced = replace(obs, qc_status=QcStatus.QC_PASSED, value=10.0)
        store.store_observations([obs_qced])

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
        assert fetched[0].qc_status == QcStatus.QC_PASSED


class TestStoreRawDuplicateSkip:
    def test_second_insert_returns_empty_and_no_new_rows(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=12)
        store = PgObservationStore(db_connection)

        obs = _raw(sid, hour=0)

        first_ids = store.store_raw_observations([obs])
        assert len(first_ids) == 1

        second_ids = store.store_raw_observations([obs])
        assert second_ids == []

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
