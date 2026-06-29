"""LOCKED regression tests for the obs-ingest-upsert restatement bug.

Milestone: obs-ingest-upsert-cadence.

Root cause: ``store_raw_observations`` used ``on_conflict_do_nothing`` on the
``(station_id, timestamp, parameter, source)`` natural key, so a BAFU
restatement (same measurementTime, new value) was silently dropped — the
stored value stayed stale and no re-QC happened.

Correct behaviour (asserted here):
  * A genuine value CHANGE upserts the new value, resets qc_status -> RAW and
    qc_flags / qc_rule_version -> NULL, and counts as a write (the updated id
    is returned).
  * An UNCHANGED re-ingest performs no row write and leaves an existing
    qc_passed row untouched (no QC churn).
  * A bulk batch with two rows sharing the same natural key is de-duplicated
    last-wins in Python (no Postgres CardinalityViolation) -> exactly one row.
  * Distinct measurementTimes still produce distinct rows (non-regression).
  * The store emits a neutral ``observation.raw_upsert`` structlog event
    carrying inserted / updated / skipped derived from a uuid4 set-diff.

These tests MUST FAIL against the buggy ``on_conflict_do_nothing``
implementation and pass only once the upsert is correct.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import StationId

from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config


def _seed_station(
    conn: sa.Connection, rng_seed: int = 1, code: str = "UPSERT-001"
) -> StationId:
    station = make_station_config(code=code, rng=random.Random(rng_seed))
    PgStationStore(conn).store_station(station)
    return station.id


def _utc(hour: int = 0):
    return ensure_utc(datetime(2025, 1, 1, hour, tzinfo=UTC))


def _raw(station_id: StationId, hour: int, value: float) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=_utc(hour=hour),
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
    )


def _passed_flag() -> QcFlag:
    return QcFlag(
        rule_id="range_check",
        rule_version="1.0",
        status=QcStatus.QC_PASSED,
        detail=None,
    )


class TestStoreRawUpsertRestatement:
    def test_changed_value_updates_value_and_resets_qc_and_counts_write(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=21)
        store = PgObservationStore(db_connection)

        [oid] = store.store_raw_observations([_raw(sid, hour=0, value=10.0)])
        # Simulate the in-flow QC pass marking the row qc_passed.
        store.update_qc(
            oid, QcStatus.QC_PASSED, [_passed_flag()], qc_rule_version="1.0"
        )

        # BAFU restatement: same natural key, different value.
        written = store.store_raw_observations([_raw(sid, hour=0, value=20.0)])

        # The restatement counts as a write.
        assert len(written) == 1

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
        obs = fetched[0]
        # Stored value becomes the restated value.
        assert obs.value == 20.0
        # qc_status reset to RAW; qc_flags / qc_rule_version cleared.
        assert obs.qc_status == QcStatus.RAW
        assert obs.qc_flags == []
        assert obs.qc_rule_version is None

    def test_unchanged_value_no_write_and_no_qc_churn(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=22)
        store = PgObservationStore(db_connection)

        [oid] = store.store_raw_observations([_raw(sid, hour=0, value=10.0)])
        store.update_qc(
            oid, QcStatus.QC_PASSED, [_passed_flag()], qc_rule_version="1.0"
        )

        # Identical re-ingest: same natural key AND same value.
        written = store.store_raw_observations([_raw(sid, hour=0, value=10.0)])

        # No row write.
        assert written == []

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
        obs = fetched[0]
        assert obs.value == 10.0
        # The qc_passed row stays qc_passed — no reset, no churn.
        assert obs.qc_status == QcStatus.QC_PASSED
        assert obs.qc_rule_version == "1.0"

    def test_bulk_batch_same_natural_key_dedups_last_wins(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=23)
        store = PgObservationStore(db_connection)

        # A single onboarding batch with two rows sharing the natural key.
        # Must NOT raise a Postgres CardinalityViolation; de-duplicated
        # last-wins in Python so exactly one row results.
        written = store.store_raw_observations(
            [
                _raw(sid, hour=0, value=10.0),
                _raw(sid, hour=0, value=20.0),
            ]
        )

        assert len(written) == 1

        fetched = store.fetch_observations(
            station_id=sid,
            parameter="discharge",
            start=_utc(hour=0),
            end=_utc(hour=1),
        )
        assert len(fetched) == 1
        # Last row in the batch wins.
        assert fetched[0].value == 20.0

    def test_distinct_timestamps_still_produce_distinct_rows(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=24)
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


class TestStoreRawUpsertObservability:
    def test_raw_upsert_event_reports_inserted_updated_skipped(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, rng_seed=25)
        store = PgObservationStore(db_connection)

        # Pre-populate two committed rows (A unchanged target, B update target).
        store.store_raw_observations(
            [
                _raw(sid, hour=0, value=10.0),  # A
                _raw(sid, hour=1, value=10.0),  # B
            ]
        )

        with structlog.testing.capture_logs() as captured:
            store.store_raw_observations(
                [
                    _raw(sid, hour=0, value=10.0),  # A: unchanged -> skipped
                    _raw(sid, hour=1, value=99.0),  # B: changed   -> updated
                    _raw(sid, hour=2, value=12.0),  # C: new        -> inserted
                ]
            )

        events = [e for e in captured if e.get("event") == "observation.raw_upsert"]
        assert len(events) == 1, (
            "store must emit a neutral observation.raw_upsert structlog event"
        )
        event = events[0]
        assert event["inserted"] == 1
        assert event["updated"] == 1
        assert event["skipped"] == 1
