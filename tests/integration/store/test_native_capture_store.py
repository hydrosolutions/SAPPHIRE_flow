from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    native_capture_batches,
    native_measurement_captures,
    observations,
)
from sapphire_flow.store.native_capture_store import PgNativeCaptureStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CaptureBatchContext,
    CaptureBatchId,
    CapturedLevelReference,
    CaptureRequestInterval,
    DhmRequestEvidence,
    NativeFetchOutcome,
    accepted_capture_input_digest,
)
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.native_capture import CaptureCommit
    from sapphire_flow.types.station import StationConfig

NOW = ensure_utc(datetime(2026, 11, 3, 12, tzinfo=UTC))
START = ensure_utc(datetime(2026, 11, 3, 11, tzinfo=UTC))


def seed_station(engine: sa.Engine) -> StationConfig:
    station = make_station_config(
        station_id=StationId(uuid4()),
        code=str(uuid4()),
        network="dhm",
        water_level_unit="m",
        measured_parameters=frozenset({"water_level"}),
    )
    with engine.begin() as conn:
        PgStationStore(conn).store_station(station)
    return station


def inputs(
    station: StationConfig, value: float = 1.2
) -> tuple[CaptureBatchContext, tuple[RawObservation, ...]]:
    rows = (
        RawObservation(
            station_id=station.id,
            timestamp=START,
            parameter="water_level",
            value=value,
            source=ObservationSource.MEASURED,
        ),
    )
    request = DhmRequestEvidence(
        batch_id=CaptureBatchId(uuid4()),
        station_id=station.id,
        provider=CanonicalProviderIdentity.parse(
            "https://provider.example/api/v1/", 66
        ),
        requested_intervals=(
            CaptureRequestInterval(
                lower_exclusive=START - timedelta(seconds=1),
                upper_exclusive=NOW + timedelta(seconds=1),
            ),
        ),
        request_started_at=NOW,
        retrieved_at=NOW,
        outcome=NativeFetchOutcome.COMPLETE,
        request_count=1,
        accepted_bytes=10,
    )
    return CaptureBatchContext(
        request=request,
        tenant_id=station.tenant_id,
        flow_run_id=None,
        accepted_input_sha256=accepted_capture_input_digest(rows),
        level_reference=CapturedLevelReference.UNKNOWN,
    ), rows


def writer(engine: sa.Engine) -> PgNativeCaptureStore:
    return PgNativeCaptureStore(engine.begin, clock=lambda: NOW, id_gen=uuid4)


class TestPgNativeCaptureStore:
    def test_unmeasured_transport_counters_remain_null(
        self,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        import json

        engine = isolated_committed_db_engine
        context, rows = inputs(seed_station(engine))
        context = replace(
            context,
            request=replace(context.request, request_count=None, accepted_bytes=None),
        )
        writer(engine).commit_batch(context, rows)
        with engine.connect() as conn:
            content = conn.execute(
                sa.select(native_capture_batches.c.context_content).where(
                    native_capture_batches.c.id == context.request.batch_id
                )
            ).scalar_one()
        stored = json.loads(content)
        assert stored["request_count"] is None
        assert stored["accepted_bytes"] is None

    def test_lost_ack_retry_cannot_regress_newer_correction_and_qc(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        store = writer(engine)
        batch_a, rows_a = inputs(station)
        original = store.commit_batch(batch_a, rows_a)
        batch_b, rows_b = inputs(station, 2.2)
        corrected = store.commit_batch(batch_b, rows_b)
        with engine.begin() as conn:
            PgObservationStore(conn).update_qc(
                corrected.written_ids[0], QcStatus.QC_PASSED, [], qc_rule_version="test"
            )
        assert store.commit_batch(batch_a, rows_a) == original
        with engine.connect() as conn:
            current = PgObservationStore(conn).fetch_observation_by_id(
                original.written_ids[0]
            )
            assert current is not None
            assert (current.value, current.qc_status) == (2.2, QcStatus.QC_PASSED)

    def test_new_unchanged_fetch_reuses_capture_without_qc_churn(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        store = writer(engine)
        batch, rows = inputs(station)
        first = store.commit_batch(batch, rows)
        with engine.begin() as conn:
            PgObservationStore(conn).update_qc(
                first.written_ids[0], QcStatus.QC_PASSED, [], qc_rule_version="test"
            )
        next_batch = replace(
            batch, request=replace(batch.request, batch_id=CaptureBatchId(uuid4()))
        )
        second = store.commit_batch(next_batch, rows)
        assert second.observed_versions == first.observed_versions
        assert second.unchanged_count == 1
        with engine.connect() as conn:
            current = PgObservationStore(conn).fetch_observation_by_id(
                first.written_ids[0]
            )
            assert current is not None and current.qc_status is QcStatus.QC_PASSED

    def test_same_batch_different_rows_fails_before_raw_write(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        store = writer(engine)
        batch, rows = inputs(station)
        first = store.commit_batch(batch, rows)
        changed = (replace(rows[0], value=9.9),)
        reused = replace(
            batch, accepted_input_sha256=accepted_capture_input_digest(changed)
        )
        with pytest.raises(ValueError, match="reused with different input"):
            store.commit_batch(reused, changed)
        with engine.connect() as conn:
            current = PgObservationStore(conn).fetch_observation_by_id(
                first.written_ids[0]
            )
            assert current is not None and current.value == 1.2

    def test_a_b_a_new_fetch_preserves_both_source_versions(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        store = writer(engine)
        batch_a, rows_a = inputs(station)
        first = store.commit_batch(batch_a, rows_a)
        batch_b, rows_b = inputs(station, 2.2)
        store.commit_batch(batch_b, rows_b)
        new_a, new_rows = inputs(station)
        third = store.commit_batch(new_a, new_rows)
        assert third.observed_versions == first.observed_versions
        with engine.connect() as conn:
            count = conn.scalar(
                sa.select(sa.func.count())
                .select_from(native_measurement_captures)
                .where(native_measurement_captures.c.station_id == station.id)
            )
            assert count == 2

    def test_audit_failure_rolls_back_raw_and_capture(
        self, isolated_committed_db_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.store.audit_log_store import PgAuditLogStore

        def fail_audit(self: object, entry: object) -> None:
            raise ValueError("injected audit failure")

        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        monkeypatch.setattr(PgAuditLogStore, "append_entry", fail_audit)
        with pytest.raises(ValueError, match="injected audit failure"):
            writer(engine).commit_batch(batch, rows)
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(observations)
                    .where(observations.c.station_id == station.id)
                )
                == 0
            )
            assert (
                conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(native_measurement_captures)
                    .where(native_measurement_captures.c.station_id == station.id)
                )
                == 0
            )
            assert (
                conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(native_capture_batches)
                    .where(native_capture_batches.c.station_id == station.id)
                )
                == 0
            )

    def test_concurrent_identical_retry_commits_one_capture_batch(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        barrier = Barrier(2)

        def commit() -> CaptureCommit:
            barrier.wait(timeout=10)
            return writer(engine).commit_batch(batch, rows)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(commit)
            second = pool.submit(commit)
            assert first.result(timeout=20) == second.result(timeout=20)
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.select(sa.func.count())
                    .select_from(native_capture_batches)
                    .where(native_capture_batches.c.id == batch.request.batch_id)
                )
                == 1
            )

    def test_recovery_targets_only_current_captured_raw_versions(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        store = writer(engine)
        original = store.commit_batch(batch, rows)
        recovered = store.pending_qc(station.id, start=START, end=NOW, limit=1000)
        assert (
            tuple(row.observation_id for row in recovered.current_raw)
            == original.written_ids
        )
        with engine.begin() as conn:
            PgObservationStore(conn).store_raw_observations(
                [replace(rows[0], value=7.7)]
            )
        assert (
            store.pending_qc(station.id, start=START, end=NOW, limit=1000).current_raw
            == ()
        )
        changed_batch, changed_rows = inputs(station, 7.7)
        current = store.commit_batch(changed_batch, changed_rows)
        recovered = store.pending_qc(station.id, start=START, end=NOW, limit=1000)
        assert tuple(row.capture_id for row in recovered.current_raw) == tuple(
            row.id for row in current.observed_versions
        )
        with engine.begin() as conn:
            PgObservationStore(conn).update_qc(
                original.written_ids[0],
                QcStatus.QC_UNCHECKED,
                [],
                qc_rule_version="test",
            )
        assert (
            store.pending_qc(station.id, start=START, end=NOW, limit=1000).current_raw
            == ()
        )

    def test_recovery_counts_older_current_raw_outside_empty_window(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        store = writer(engine)
        store.commit_batch(batch, rows)
        recovered = store.pending_qc(
            station.id, start=NOW, end=NOW + timedelta(hours=1), limit=1000
        )
        assert recovered.current_raw == ()
        assert recovered.beyond_window_count == 1

    def test_recovery_context_is_bounded_and_revalidates_captured_version(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        store = writer(engine)
        store.commit_batch(batch, rows)
        recovery = store.pending_qc(station.id, start=START, end=NOW, limit=1000)
        with engine.begin() as conn:
            qc_reader = PgObservationStore(conn).qc_context_reader()
            context = qc_reader.fetch_capture_qc_window(
                recovery, context_window_hours=2
            )
            assert context.target_ids == frozenset(
                row.observation_id for row in recovery.current_raw
            )
            assert all(row.qc_read_token is not None for row in context.observations)
            PgObservationStore(conn).store_raw_observations(
                [replace(rows[0], value=8.8)]
            )
            changed = qc_reader.fetch_capture_qc_window(
                recovery, context_window_hours=2
            )
            assert changed.target_ids == frozenset()

    def test_recovery_context_density_cap_includes_neighbors(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        from sapphire_flow.types.rating_reconciliation import RatingInputsUnavailable

        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        store = writer(engine)
        store.commit_batch(batch, rows)
        recovery = store.pending_qc(station.id, start=START, end=NOW, limit=1000)
        with engine.begin() as conn:
            PgObservationStore(conn).store_raw_observations(
                [
                    replace(rows[0], timestamp=START - timedelta(seconds=index + 1))
                    for index in range(1000)
                ]
            )
            with pytest.raises(RatingInputsUnavailable, match="chunk_limit"):
                PgObservationStore(conn).qc_context_reader().fetch_capture_qc_window(
                    recovery,
                    context_window_hours=2,
                )

    def test_recovery_only_qc_does_not_touch_now_or_neighbor_targets(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        from sapphire_flow.flows.ingest_observations import QcWindowMode, _run_qc_task
        from sapphire_flow.types.domain import QcRuleSet
        from tests.fakes.fake_stores import FakeClimBaselineStore

        engine = isolated_committed_db_engine
        station = seed_station(engine)
        batch, rows = inputs(station)
        store = writer(engine)
        committed = store.commit_batch(batch, rows)
        recovery = store.pending_qc(station.id, start=START, end=NOW, limit=1000)
        with engine.begin() as conn:
            neighbors = PgObservationStore(conn).store_raw_observations(
                [
                    replace(rows[0], timestamp=START - timedelta(minutes=5)),
                    replace(rows[0], timestamp=NOW + timedelta(days=10)),
                ]
            )
            outcome = _run_qc_task.fn(
                PgObservationStore(conn).qc_context_reader(),
                FakeClimBaselineStore(),
                station.id,
                "water_level",
                QcRuleSet(version="none", rules=()),
                NOW + timedelta(days=10),
                station_networks={station.id: "dhm"},
                recovery_windows=(recovery,),
                window_mode=QcWindowMode.RECOVERY_ONLY,
            )
            assert outcome.evaluated_ids == committed.written_ids
            assert outcome.counts["newly_checked"] == 1
            assert all(
                PgObservationStore(conn).fetch_observation_by_id(identity).qc_status
                is QcStatus.RAW
                for identity in neighbors
            )
