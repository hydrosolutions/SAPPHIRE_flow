from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    bafu_unit_batch_receipts as receipts,
)
from sapphire_flow.db.metadata import (
    bafu_unit_events as events,
)
from sapphire_flow.db.metadata import (
    bafu_unit_read_holds as holds,
)
from sapphire_flow.db.metadata import (
    observations,
)
from sapphire_flow.exceptions import DeliveryCollisionError
from sapphire_flow.store.bafu_batch_journal import (
    BafuBatchIntegrityError,
    PgBafuBatchJournal,
)
from sapphire_flow.store.bafu_unit_writer import PgBafuUnitWriter
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
from sapphire_flow.types.bafu_capture_batch import (
    BafuCaptureBatchId,
    BafuStationCaptureBatch,
)
from sapphire_flow.types.bafu_unit_registry import (
    BafuUnitRegistry,
    BafuUnitRule,
    ResolvedBafuUnit,
    UnitRefreshState,
)
from sapphire_flow.types.bafu_write_result import (
    BafuApplicability,
    BafuCapacityError,
    BafuCommitAcknowledgementError,
    BafuJournalQuota,
    BafuWriteStatus,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.observation_units import (
    DischargeSourceUnit,
    SourceDischargeValue,
)
from tests.integration.store import test_bafu_batch_journal

batch = test_bafu_batch_journal.batch

if TYPE_CHECKING:
    from collections.abc import Iterator

QUOTA = BafuJournalQuota(
    max_station_events=100,
    max_station_receipts=100,
    max_station_evidence_bytes=1_000_000,
    max_batch_bytes=100_000,
)


def normalized(
    candidate: BafuStationCaptureBatch,
    literal: str = "22",
    *,
    unit: DischargeSourceUnit = DischargeSourceUnit.LITRES_PER_SECOND,
    policy: str = "first",
) -> BafuStationCaptureBatch:
    rule = BafuUnitRule(
        rule_id=policy,
        key=candidate.key,
        unit=unit,
        valid_from=ensure_utc(candidate.received_at - timedelta(days=1)),
        valid_until=None,
        verified_at=ensure_utc(candidate.received_at - timedelta(days=1)),
        refresh_due=ensure_utc(candidate.received_at + timedelta(days=10)),
        evidence_reference="saved-fixture",
        evidence_sha256="e" * 64,
    )
    capture = replace(
        candidate.captures[0],
        source_literals=(literal,),
        result=NormalizedDischargeCapture(
            measurement_time=candidate.captures[0].result.measurement_time,
            source_value=SourceDischargeValue.from_literal(literal),
            resolution=ResolvedBafuUnit(rule=rule, refresh=UnitRefreshState.CURRENT),
        ),
    )
    observation = RawObservation(
        station_id=candidate.station_id,
        timestamp=capture.result.measurement_time,
        parameter="discharge",
        value=capture.result.canonical_value.value,
        source=ObservationSource.MEASURED,
    )
    return replace(
        candidate,
        registry=BafuUnitRegistry(version=policy, rules=(rule,)),
        captures=(capture,),
        observations=(observation,),
    )


def later(
    candidate: BafuStationCaptureBatch, *, literal: str | None = None
) -> BafuStationCaptureBatch:
    received = ensure_utc(candidate.received_at + timedelta(seconds=1))
    capture = replace(candidate.captures[0], received_at=received)
    observations_ = candidate.observations
    if literal is not None:
        assert isinstance(capture.result, NormalizedDischargeCapture)
        capture = replace(
            capture,
            source_literals=(literal,),
            result=replace(
                capture.result, source_value=SourceDischargeValue.from_literal(literal)
            ),
        )
        observations_ = tuple(
            replace(obs, value=capture.result.canonical_value.value)
            if obs.parameter == "discharge"
            else obs
            for obs in observations_
        )
    return replace(
        candidate,
        batch_id=BafuCaptureBatchId(uuid4()),
        received_at=received,
        captures=(capture,),
        observations=observations_,
    )


def rows(
    engine: sa.Engine, table: sa.Table, candidate: BafuStationCaptureBatch
) -> list[sa.RowMapping]:
    with engine.connect() as conn:
        predicate = (
            table.c.batch_id == candidate.batch_id
            if table is receipts
            else table.c.station_id == candidate.station_id
        )
        return list(conn.execute(sa.select(table).where(predicate)).mappings())


def mark_passed(engine: sa.Engine, candidate: BafuStationCaptureBatch) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(observations)
            .where(observations.c.station_id == candidate.station_id)
            .values(qc_status="qc_passed", qc_rule_version="test-rule", qc_flags=[])
        )


class TestPgBafuUnitWriter:
    def test_insert_retry_and_lexical_repeat_preserve_qc(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        candidate = normalized(batch)
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = writer.write(candidate)
        assert first.status is BafuWriteStatus.COMMITTED
        assert first.counts.inserted == 1
        mark_passed(engine, candidate)
        retry = writer.write(candidate)
        assert retry.status is BafuWriteStatus.REPLAYED
        assert retry.result_digest == first.result_digest
        assert retry.applicability is BafuApplicability.CURRENT
        repeat = writer.write(later(candidate, literal="22.000"))
        assert repeat.status is BafuWriteStatus.CURRENT
        assert repeat.counts.unchanged == 1
        assert len(rows(engine, events, candidate)) == 1
        assert rows(engine, observations, candidate)[0]["qc_status"] == "qc_passed"
        assert rows(engine, observations, candidate)[0]["value"] == 0.022

    def test_quarantine_only_is_durable_and_stale_polls_deduplicate(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = writer.write(batch)
        assert first.counts.quarantined == 1
        assert rows(engine, observations, batch) == []
        assert len(rows(engine, holds, batch)) == 1
        assert len(rows(engine, receipts, batch)) == 1
        repeat = later(batch)
        assert writer.write(repeat).status is BafuWriteStatus.CURRENT
        assert rows(engine, receipts, repeat) == []
        assert len(rows(engine, events, batch)) == 1

    def test_water_level_with_withheld_discharge(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        level = RawObservation(
            station_id=batch.station_id,
            timestamp=batch.received_at,
            parameter="water_level",
            value=4.2,
            source=ObservationSource.MEASURED,
        )
        candidate = replace(batch, observations=(level,))
        result = PgBafuUnitWriter(
            isolated_committed_db_engine.begin, quota=QUOTA
        ).write(candidate)
        assert result.status is BafuWriteStatus.COMMITTED
        assert [
            row["parameter"]
            for row in rows(isolated_committed_db_engine, observations, candidate)
        ] == ["water_level"]

    @pytest.mark.parametrize("point", ["after_value", "before_commit"])
    def test_fault_rolls_back_all_effects(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        point: str,
    ) -> None:
        def fault(label: str) -> None:
            if label == point:
                raise RuntimeError("injected fault")

        candidate = normalized(batch)
        writer = PgBafuUnitWriter(
            isolated_committed_db_engine.begin, quota=QUOTA, fault_point=fault
        )
        with pytest.raises(RuntimeError, match="injected fault"):
            writer.write(candidate)
        for table in (events, receipts, holds, observations):
            assert rows(isolated_committed_db_engine, table, candidate) == []
        assert writer.reconcile(candidate).status is BafuWriteStatus.NOT_COMMITTED

    def test_quota_rolls_back_usable_parameter_too(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        level = RawObservation(
            station_id=batch.station_id,
            timestamp=batch.received_at,
            parameter="water_level",
            value=4.2,
            source=ObservationSource.MEASURED,
        )
        candidate = replace(batch, observations=(level,))
        writer = PgBafuUnitWriter(
            isolated_committed_db_engine.begin, quota=replace(QUOTA, max_batch_bytes=1)
        )
        with pytest.raises(BafuCapacityError, match="entire batch rolled back"):
            writer.write(candidate)
        for table in (events, receipts, holds, observations):
            assert rows(isolated_committed_db_engine, table, candidate) == []

    def test_same_id_mismatch_fails(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        writer = PgBafuUnitWriter(isolated_committed_db_engine.begin, quota=QUOTA)
        writer.write(batch)
        with pytest.raises(BafuBatchIntegrityError, match="different captured input"):
            writer.write(
                replace(batch, registry=replace(batch.registry, version="changed"))
            )

    def test_a_b_a_is_new_revision_and_old_retry_cannot_undo(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        a = normalized(batch)
        writer.write(a)
        b = later(a, literal="44")
        result = writer.write(b)
        assert result.counts.restated == 1
        assert writer.write(a).applicability is BafuApplicability.SUPERSEDED
        assert rows(engine, observations, batch)[0]["value"] == 0.044
        again = writer.write(later(b, literal="22"))
        assert again.counts.restated == 1
        assert len(rows(engine, events, batch)) == 3
        assert writer.write(a).applicability is BafuApplicability.SUPERSEDED

    def test_legacy_changed_collision_is_rejected_not_repaired(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        candidate = normalized(batch)
        engine = isolated_committed_db_engine
        with engine.begin() as conn:
            PgObservationStore(conn).store_raw_observations(
                [replace(candidate.observations[0], value=22)]
            )
        mark_passed(engine, candidate)
        result = PgBafuUnitWriter(engine.begin, quota=QUOTA).write(candidate)
        assert result.counts.quarantined == 1
        assert rows(engine, observations, candidate)[0]["value"] == 22
        assert rows(engine, observations, candidate)[0]["qc_status"] == "qc_passed"
        assert len(rows(engine, holds, candidate)) == 1

    @pytest.mark.parametrize(
        "literal,unit,expected",
        [
            ("0", DischargeSourceUnit.LITRES_PER_SECOND, "raw"),
            ("17.645", DischargeSourceUnit.CUBIC_METRES_PER_SECOND, "qc_passed"),
        ],
    )
    def test_first_equal_evidence_qc_semantics(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        literal: str,
        unit: DischargeSourceUnit,
        expected: str,
    ) -> None:
        candidate = normalized(batch, literal, unit=unit)
        engine = isolated_committed_db_engine
        with engine.begin() as conn:
            PgObservationStore(conn).store_raw_observations(
                list(candidate.observations)
            )
        mark_passed(engine, candidate)
        result = PgBafuUnitWriter(engine.begin, quota=QUOTA).write(candidate)
        assert result.counts.evidence_only == 1
        assert rows(engine, observations, candidate)[0]["qc_status"] == expected
        event = rows(engine, events, candidate)[0]
        assert event["before_state"]["qc_status"] == "qc_passed"
        assert event["after_state"]["qc_status"] == expected

    def test_delivery_collision_preserved(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        candidate = normalized(batch)
        with isolated_committed_db_engine.begin() as conn:
            PgObservationStore(conn).store_raw_observations(
                [replace(candidate.observations[0], delivery_id="owned")]
            )
        with pytest.raises(DeliveryCollisionError, match="delivery"):
            PgBafuUnitWriter(isolated_committed_db_engine.begin, quota=QUOTA).write(
                candidate
            )
        assert rows(isolated_committed_db_engine, events, candidate) == []

    def test_newer_start_blocks_old_even_when_new_write_failed(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        older = normalized(batch)
        PgBafuBatchJournal(engine.begin).admit(older)
        newer = later(older)
        with pytest.raises(BafuCapacityError, match="quota"):
            PgBafuUnitWriter(
                engine.begin, quota=replace(QUOTA, max_batch_bytes=1)
            ).write(newer)
        assert (
            PgBafuUnitWriter(engine.begin, quota=QUOTA).write(older).status
            is BafuWriteStatus.SUPERSEDED
        )
        assert rows(engine, observations, older) == []

    def test_uncertain_commit_uses_fresh_read_without_rewrite(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        calls = 0

        @contextmanager
        def factory() -> Iterator[sa.Connection]:
            nonlocal calls
            calls += 1
            with isolated_committed_db_engine.begin() as conn:
                yield conn
            if calls == 2:
                raise BafuCommitAcknowledgementError("lost commit response")

        result = PgBafuUnitWriter(factory, quota=QUOTA).write(normalized(batch))
        assert result.status is BafuWriteStatus.REPLAYED
        assert calls == 3
        assert len(rows(isolated_committed_db_engine, events, batch)) == 1

    def test_failed_reconciliation_is_unknown_not_absence(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        calls = 0

        @contextmanager
        def factory() -> Iterator[sa.Connection]:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise BafuCommitAcknowledgementError("cannot read")
            with isolated_committed_db_engine.begin() as conn:
                yield conn
            if calls == 2:
                raise BafuCommitAcknowledgementError("lost commit response")

        result = PgBafuUnitWriter(factory, quota=QUOTA).write(normalized(batch))
        assert result.status is BafuWriteStatus.UNKNOWN
        assert len(rows(isolated_committed_db_engine, events, batch)) == 1

    def test_negative_canonical_is_raw_for_ordinary_qc(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        candidate = normalized(batch, "-22")
        PgBafuUnitWriter(isolated_committed_db_engine.begin, quota=QUOTA).write(
            candidate
        )
        row = rows(isolated_committed_db_engine, observations, candidate)[0]
        assert row["value"] == -0.022
        assert row["qc_status"] == "raw"

    def test_metadata_refresh_keeps_proven_qc(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = normalized(batch)
        writer.write(first)
        mark_passed(engine, first)
        second = later(first)
        rule = replace(second.registry.rules[0], evidence_reference="new-proof")
        capture = replace(
            second.captures[0],
            result=replace(
                second.captures[0].result,
                resolution=ResolvedBafuUnit(
                    rule=rule, refresh=UnitRefreshState.CURRENT
                ),
            ),
        )
        second = replace(
            second,
            registry=BafuUnitRegistry(version="new", rules=(rule,)),
            captures=(capture,),
        )
        result = writer.write(second)
        assert result.counts.evidence_only == 1
        assert rows(engine, observations, first)[0]["qc_status"] == "qc_passed"

    def test_refresh_warning_and_payload_hash_are_not_revisions(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = normalized(batch)
        writer.write(first)
        second = later(first)
        rule = replace(second.registry.rules[0], refresh_due=second.received_at)
        capture = replace(
            second.captures[0],
            payload_sha256="f" * 64,
            result=replace(
                second.captures[0].result,
                resolution=ResolvedBafuUnit(rule=rule, refresh=UnitRefreshState.DUE),
            ),
        )
        second = replace(
            second,
            registry=BafuUnitRegistry(version="refresh", rules=(rule,)),
            captures=(capture,),
        )
        assert writer.write(second).status is BafuWriteStatus.CURRENT
        assert len(rows(engine, events, first)) == 1

    def test_zero_unit_context_change_resets_qc(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = normalized(batch, "0", unit=DischargeSourceUnit.CUBIC_METRES_PER_SECOND)
        writer.write(first)
        mark_passed(engine, first)
        second = later(first)
        rule = replace(
            second.registry.rules[0], unit=DischargeSourceUnit.LITRES_PER_SECOND
        )
        capture = replace(
            second.captures[0],
            result=replace(
                second.captures[0].result,
                resolution=ResolvedBafuUnit(
                    rule=rule, refresh=UnitRefreshState.CURRENT
                ),
            ),
        )
        second = replace(
            second,
            registry=BafuUnitRegistry(version="litres", rules=(rule,)),
            captures=(capture,),
        )
        result = writer.write(second)
        assert result.counts.evidence_only == 1
        assert rows(engine, observations, first)[0]["qc_status"] == "raw"

    def test_out_of_band_value_cannot_be_recertified_by_old_event(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = normalized(batch)
        writer.write(first)
        with engine.begin() as conn:
            conn.execute(
                sa.update(observations)
                .where(observations.c.station_id == first.station_id)
                .values(value=22)
            )
        assert writer.write(first).applicability is BafuApplicability.SUPERSEDED
        result = writer.write(later(first))
        assert result.counts.quarantined == 1
        assert rows(engine, observations, first)[0]["value"] == 22

    def test_quarantined_capture_can_resolve_under_new_policy(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        writer.write(batch)
        result = writer.write(normalized(later(batch)))
        assert result.counts.inserted == 1
        assert len(rows(engine, events, batch)) == 2
        assert len(rows(engine, holds, batch)) == 1  # ordinary ingest never releases

    def test_station_quota_rolls_back_second_revision(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        first = normalized(batch)
        writer = PgBafuUnitWriter(
            engine.begin, quota=replace(QUOTA, max_station_events=1)
        )
        writer.write(first)
        with pytest.raises(BafuCapacityError, match="quota"):
            writer.write(later(first, literal="44"))
        assert rows(engine, observations, first)[0]["value"] == 0.022
        assert len(rows(engine, events, first)) == 1

    def test_concurrent_same_id_returns_one_receipt(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        engine = isolated_committed_db_engine
        candidate = normalized(batch)
        barrier = Barrier(2)

        def write() -> BafuWriteStatus:
            barrier.wait(timeout=10)
            return PgBafuUnitWriter(engine.begin, quota=QUOTA).write(candidate).status

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write), executor.submit(write)]
            statuses = {future.result(timeout=15) for future in futures}
        assert statuses == {BafuWriteStatus.COMMITTED, BafuWriteStatus.REPLAYED}
        assert len(rows(engine, events, candidate)) == 1
        assert len(rows(engine, receipts, candidate)) == 1

    @pytest.mark.parametrize("fence_offset", [None, -1, 0, 1])
    def test_absent_identity_requires_real_equal_or_newer_fence(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        fence_offset: int | None,
    ) -> None:
        engine = isolated_committed_db_engine
        if fence_offset is not None:
            received = ensure_utc(batch.received_at + timedelta(seconds=fence_offset))
            fence = replace(
                batch,
                batch_id=BafuCaptureBatchId(uuid4()),
                received_at=received,
                captures=(replace(batch.captures[0], received_at=received),),
            )
            PgBafuBatchJournal(engine.begin).admit(fence)
        result = PgBafuUnitWriter(engine.begin, quota=QUOTA).reconcile(batch)
        superseded = fence_offset is not None and fence_offset >= 0
        assert result.status is (
            BafuWriteStatus.SUPERSEDED if superseded else BafuWriteStatus.NOT_COMMITTED
        )
        assert result.applicability is (
            BafuApplicability.SUPERSEDED if superseded else BafuApplicability.UNKNOWN
        )

    def test_failed_first_admission_is_not_proof_of_supersession(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        calls = 0

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            nonlocal calls
            calls += 1
            with isolated_committed_db_engine.begin() as conn:
                yield conn
                if calls == 1:
                    raise BafuCommitAcknowledgementError("admission rolled back")

        result = PgBafuUnitWriter(transaction, quota=QUOTA).write(batch)
        assert result.status is BafuWriteStatus.NOT_COMMITTED
        assert result.applicability is BafuApplicability.UNKNOWN

    def test_receipt_bytes_are_in_batch_quota(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        levels = tuple(
            RawObservation(
                station_id=batch.station_id,
                timestamp=ensure_utc(batch.received_at + timedelta(seconds=i)),
                parameter="water_level",
                value=4.2 + i,
                source=ObservationSource.MEASURED,
            )
            for i in range(100)
        )
        candidate = replace(batch, observations=levels)
        writer = PgBafuUnitWriter(
            isolated_committed_db_engine.begin,
            quota=replace(QUOTA, max_batch_bytes=3000),
        )
        with pytest.raises(BafuCapacityError, match="entire batch rolled back"):
            writer.write(candidate)
        for table in (events, receipts, holds, observations):
            assert rows(isolated_committed_db_engine, table, candidate) == []

    def test_rejected_attempt_does_not_fence_outstanding_write(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        engine = isolated_committed_db_engine
        journal = PgBafuBatchJournal(engine.begin)
        journal.admit(batch)
        rejected = replace(batch, batch_id=BafuCaptureBatchId(uuid4()))
        journal.admit(rejected)
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        assert writer.reconcile(rejected).status is BafuWriteStatus.SUPERSEDED
        assert writer.write(batch).status is BafuWriteStatus.COMMITTED
        changed = replace(
            rejected, registry=replace(rejected.registry, version="changed")
        )
        with pytest.raises(BafuBatchIntegrityError, match="different captured input"):
            writer.reconcile(changed)

    def test_no_receipt_snapshot_does_not_preclude_future_write(
        self, isolated_committed_db_engine: sa.Engine, batch: BafuStationCaptureBatch
    ) -> None:
        writer = PgBafuUnitWriter(isolated_committed_db_engine.begin, quota=QUOTA)
        PgBafuBatchJournal(isolated_committed_db_engine.begin).admit(batch)
        snapshot = writer.reconcile(batch)
        assert snapshot.status is BafuWriteStatus.NOT_COMMITTED
        assert snapshot.applicability is BafuApplicability.UNKNOWN
        assert writer.write(batch).status is BafuWriteStatus.COMMITTED


class TestReviewRegressions:
    @pytest.mark.parametrize("zone", ["UTC", "Europe/Zurich"])
    @pytest.mark.parametrize("value", [-0.0, 1e22, 1e23, 4.2, 0.12345678901234567])
    def test_jsonb_snapshot_roundtrip(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        zone: str,
        value: float,
    ) -> None:
        engine = isolated_committed_db_engine
        candidate = normalized(batch)
        candidate = replace(
            candidate,
            observations=(
                *candidate.observations,
                replace(
                    candidate.observations[0], parameter="water_level", value=value
                ),
            ),
        )

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with engine.begin() as conn:
                conn.execute(
                    sa.text("SELECT set_config('TimeZone', :zone, true)"),
                    {"zone": zone},
                )
                yield conn

        writer = PgBafuUnitWriter(transaction, quota=QUOTA)
        first = writer.write(candidate)
        assert first.status is BafuWriteStatus.COMMITTED
        utc_writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        replay = utc_writer.write(candidate)
        assert replay.status is BafuWriteStatus.REPLAYED
        assert replay.applicability is BafuApplicability.CURRENT
        assert writer.write(candidate).applicability is BafuApplicability.CURRENT
        assert utc_writer.write(later(candidate)).status is BafuWriteStatus.CURRENT
        assert writer.write(later(later(candidate))).status is BafuWriteStatus.CURRENT
        event = rows(engine, events, candidate)[0]
        assert event["after_state"]["timestamp"].endswith("Z")

    @pytest.mark.parametrize("parameter", ["discharge", "water_level"])
    @pytest.mark.parametrize("fault", ["skip", "value", "provenance", "delivery"])
    def test_write_postcondition_rolls_back(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        parameter: str,
        fault: str,
    ) -> None:
        engine = isolated_committed_db_engine
        original = normalized(batch)
        original = replace(
            original,
            observations=(
                *original.observations,
                replace(original.observations[0], parameter="water_level", value=4.2),
            ),
        )
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        writer.write(original)
        candidate = later(original, literal="44")
        candidate = replace(
            candidate,
            observations=tuple(
                replace(obs, value=8.4) if obs.parameter == "water_level" else obs
                for obs in candidate.observations
            ),
        )
        action = {
            "skip": "RETURN NULL;",
            "value": "NEW.value := 999;",
            "provenance": "NEW.rating_curve_correction_version := 'unexpected';",
            "delivery": "NEW.delivery_id := 'foreign';",
        }[fault]
        with engine.begin() as conn:
            conn.execute(
                sa.text(f"""CREATE FUNCTION review_observation_fault() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.station_id = '{candidate.station_id}'
                   AND NEW.parameter = '{parameter}' THEN
                    {action}
                END IF;
                RETURN NEW; END $$""")
            )
            conn.execute(
                sa.text(
                    "CREATE TRIGGER review_observation_fault "
                    "BEFORE UPDATE ON observations "
                    "FOR EACH ROW EXECUTE FUNCTION review_observation_fault()"
                )
            )
        try:
            error = (
                DeliveryCollisionError
                if fault == "delivery"
                else BafuBatchIntegrityError
            )
            with pytest.raises(error, match="delivery|intended observation"):
                writer.write(candidate)
            assert len(rows(engine, events, original)) == 1
            assert rows(engine, receipts, candidate) == []
            assert {
                row["parameter"]: row["value"]
                for row in rows(engine, observations, original)
            } == {"discharge": 0.022, "water_level": 4.2}
        finally:
            with engine.begin() as conn:
                conn.execute(
                    sa.text("DROP TRIGGER review_observation_fault ON observations")
                )
                conn.execute(sa.text("DROP FUNCTION review_observation_fault()"))

    @pytest.mark.parametrize("corruption", ["receipt", "capture", "missing_event"])
    def test_stored_corruption_is_integrity_error(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
        corruption: str,
    ) -> None:
        from sapphire_flow.store.bafu_unit_documents import document_digest

        engine = isolated_committed_db_engine
        candidate = normalized(batch)
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        writer.write(candidate)
        # Owner-only fault injection in the disposable database, not runtime grants.
        with engine.begin() as conn:
            if corruption == "capture":
                conn.execute(
                    sa.text(
                        "ALTER TABLE bafu_unit_events "
                        "DISABLE TRIGGER bafu_event_immutable"
                    )
                )
                conn.execute(
                    sa.update(events)
                    .where(events.c.batch_id == candidate.batch_id)
                    .values(capture={})
                )
                conn.execute(
                    sa.text(
                        "ALTER TABLE bafu_unit_events "
                        "ENABLE TRIGGER bafu_event_immutable"
                    )
                )
            else:
                doc = dict(rows(engine, receipts, candidate)[0]["result"])
                if corruption == "receipt":
                    doc["counts"] = {}
                else:
                    doc["event_ids"] = [9223372036854775807]
                conn.execute(
                    sa.text(
                        "ALTER TABLE bafu_unit_batch_receipts "
                        "DISABLE TRIGGER bafu_receipt_immutable"
                    )
                )
                conn.execute(
                    sa.update(receipts)
                    .where(receipts.c.batch_id == candidate.batch_id)
                    .values(result=doc, result_digest=document_digest(doc))
                )
                conn.execute(
                    sa.text(
                        "ALTER TABLE bafu_unit_batch_receipts "
                        "ENABLE TRIGGER bafu_receipt_immutable"
                    )
                )
        for operation in (writer.write, writer.reconcile):
            with pytest.raises(
                BafuBatchIntegrityError, match="malformed stored|missing event"
            ):
                operation(candidate)
        if corruption == "capture":
            with pytest.raises(BafuBatchIntegrityError, match="malformed stored"):
                writer.write(later(candidate))

    def test_quarantine_interrupts_proof_and_hold_survives_new_evidence(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        from sapphire_flow.store.bafu_unit_read_filter import trusted_unit_predicate

        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = normalized(batch)
        writer.write(first)
        quarantine = later(batch)
        writer.write(quarantine)
        changed = normalized(later(quarantine), "44")
        changed_result = writer.write(changed)
        assert changed_result.counts is not None
        assert changed_result.counts.quarantined == 1
        assert rows(engine, observations, batch)[0]["value"] == 0.022
        equal = normalized(later(changed), "22")
        equal_result = writer.write(equal)
        assert equal_result.counts is not None
        assert equal_result.counts.evidence_only == 1
        restated_result = writer.write(later(equal, literal="44"))
        assert restated_result.counts is not None
        assert restated_result.counts.restated == 1
        with engine.connect() as conn:
            visible = conn.execute(
                sa.select(observations.c.value).where(
                    observations.c.station_id == batch.station_id,
                    trusted_unit_predicate(observations),
                )
            ).all()
        assert visible == []

    def test_receipt_may_reference_earlier_unchanged_event(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        engine = isolated_committed_db_engine
        writer = PgBafuUnitWriter(engine.begin, quota=QUOTA)
        first = normalized(batch)
        initial = writer.write(first)
        second = later(first)
        second = replace(
            second,
            observations=(
                *second.observations,
                replace(second.observations[0], parameter="water_level", value=4.2),
            ),
        )
        result = writer.write(second)
        assert result.status is BafuWriteStatus.COMMITTED
        assert result.event_ids == initial.event_ids
        assert writer.reconcile(second).applicability is BafuApplicability.CURRENT

    def test_closed_database_connection_is_unknown(
        self,
        isolated_committed_db_engine: sa.Engine,
        batch: BafuStationCaptureBatch,
    ) -> None:
        @contextmanager
        def unavailable() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.connect() as conn:
                conn.close()
                yield conn

        result = PgBafuUnitWriter(unavailable, quota=QUOTA).reconcile(batch)
        assert result.status is BafuWriteStatus.UNKNOWN
        assert result.applicability is BafuApplicability.UNKNOWN
