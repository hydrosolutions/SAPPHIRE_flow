"""LOCKED regression tests for in-flow restatement handling.

Milestone: obs-ingest-upsert-cadence.

The ingest flow's store step must upsert a BAFU restatement (same natural
key, new value) and let the in-flow QC task re-QC the reset row; an identical
re-ingest must write nothing and cause no QC churn. The flow's
``observations_stored`` must count an updated row as a write (stored =
inserted + updated), and ``observations_skipped`` must exclude genuine writes.

These tests MUST FAIL while ``store_raw_observations`` drops same-natural-key
restatements, and pass only once the upsert + counting are correct.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.flows.ingest_observations import ingest_observations_flow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleParams, QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.observation import RawObservation

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId

from tests.conftest import make_station_config
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2026, 4, 8, 14, 20, tzinfo=UTC))

_QC_RULES = QcRuleSet(
    version="test",
    rules=(
        QcRuleParams(
            rule_id="range_check",
            rule_version="1.0",
            parameter="discharge",
            time_step=timedelta(seconds=600),
            thresholds={"value_min": 0.0, "value_max": 5000.0},
        ),
    ),
)


def _fixed_clock() -> UtcDatetime:
    return _NOW


def _obs(station_id: StationId, value: float) -> RawObservation:
    # offset 1 minute so timestamp < now (fetch upper bound is exclusive).
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(_NOW - timedelta(minutes=1)),
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
    )


class TestIngestRestatement:
    def test_restatement_updates_value_and_reqcs(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        station_store = FakeStationStore()
        station_store.store_station(s1)

        obs_store = FakeObservationStore()
        # Original reading at T, marked qc_passed by a prior cycle.
        original = _obs(s1.id, 110.0)
        [oid] = obs_store.store_raw_observations([original])
        obs_store.update_qc(oid, QcStatus.QC_PASSED, [])

        # BAFU restates the SAME measurementTime with a new value.
        restated = _obs(s1.id, 130.0)

        with structlog.testing.capture_logs() as captured:
            result = ingest_observations_flow(
                station_store=station_store,
                obs_store=obs_store,
                baseline_store=FakeClimBaselineStore(),
                adapter=FakeStationDataSource([restated]),
                qc_rules=_QC_RULES,
                clock=_fixed_clock,
            )

        # The restatement is counted as a write, not a skip.
        assert result.observations_stored == 1
        assert result.observations_skipped == 0
        # Reset row was re-QC'd in the same run.
        assert result.qc_passed == 1

        # Observability: the flow event reports stored = inserted + updated with
        # skipped excluding the genuine write.
        events = [e for e in captured if e.get("event") == "ingest.store_complete"]
        assert len(events) == 1
        assert events[0]["stored"] == 1
        assert events[0]["skipped"] == 0

        rows = obs_store.observations()
        assert len(rows) == 1
        assert rows[0].value == 130.0
        assert rows[0].qc_status == QcStatus.QC_PASSED

    def test_identical_reingest_no_write_and_no_qc_churn(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        station_store = FakeStationStore()
        station_store.store_station(s1)

        obs_store = FakeObservationStore()
        original = _obs(s1.id, 110.0)
        [oid] = obs_store.store_raw_observations([original])
        obs_store.update_qc(oid, QcStatus.QC_PASSED, [])

        # Pre-condition (RED while store_raw_observations drops restatements):
        # a genuine restatement MUST upsert + re-QC so a qc_passed row exists at
        # the restated value before we exercise the identical-re-ingest path.
        precondition = ingest_observations_flow(
            station_store=station_store,
            obs_store=obs_store,
            baseline_store=FakeClimBaselineStore(),
            adapter=FakeStationDataSource([_obs(s1.id, 130.0)]),
            qc_rules=_QC_RULES,
            clock=_fixed_clock,
        )
        assert precondition.observations_stored == 1
        seeded = obs_store.observations()
        assert len(seeded) == 1
        assert seeded[0].value == 130.0
        assert seeded[0].qc_status == QcStatus.QC_PASSED

        # Identical re-ingest: same natural key AND same (restated) value.
        with structlog.testing.capture_logs() as captured:
            result = ingest_observations_flow(
                station_store=station_store,
                obs_store=obs_store,
                baseline_store=FakeClimBaselineStore(),
                adapter=FakeStationDataSource([_obs(s1.id, 130.0)]),
                qc_rules=_QC_RULES,
                clock=_fixed_clock,
            )

        assert result.observations_stored == 0
        assert result.observations_skipped == 1
        # No new RAW row -> no QC churn.
        assert result.qc_passed == 0

        # Observability: the flow event reports the no-op as skipped, not stored.
        events = [e for e in captured if e.get("event") == "ingest.store_complete"]
        assert len(events) == 1
        assert events[0]["stored"] == 0
        assert events[0]["skipped"] == 1

        rows = obs_store.observations()
        assert len(rows) == 1
        assert rows[0].value == 130.0
        assert rows[0].qc_status == QcStatus.QC_PASSED
