from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest

from sapphire_flow.types.enums import QcStatus
from tests.conftest import make_observation
from tests.fakes.fake_stores import FakeObservationStore


class TestFakeUnitReaderContract:
    def test_additive_holds_and_canonical_qc_context(self) -> None:
        row = make_observation()
        store = FakeObservationStore()
        store.store_observations([row])
        store.hold_unit_observation(row.id, "one")
        store.hold_unit_observation(row.id, "two")
        store.record_unit_evidence(row)
        qc = store.qc_context_reader()
        assert store.fetch_observation_by_id(row.id) is None
        assert qc.fetch_observation_by_id(row.id) is not None
        store.release_unit_hold(row.id, "one")
        assert store.fetch_observation_by_id(row.id) is None
        store.release_unit_hold(row.id, "two")
        assert store.fetch_observation_by_id(row.id) is not None

    def test_current_provenance_mismatch_hides_old_evidence(self) -> None:
        row = make_observation()
        store = FakeObservationStore()
        store.store_observations([row])
        store.record_unit_evidence(row)
        store.store_observations([replace(row, rating_curve_correction_version="new")])
        assert store.fetch_observation_by_id(row.id) is None
        assert store.qc_context_reader().fetch_observation_by_id(row.id) is None

    def test_hold_change_invalidates_snapshot_without_numeric_change(self) -> None:
        row = make_observation(value=0.0)
        store = FakeObservationStore()
        store.store_observations([row])
        store.record_unit_evidence(row)
        qc = store.qc_context_reader()
        evaluated = qc.fetch_observations(
            row.station_id,
            row.parameter,
            row.timestamp,
            row.timestamp + timedelta(hours=1),
        )[0]
        store.hold_unit_observation(row.id, "history")
        assert not qc.update_qc_if_current(evaluated, QcStatus.QC_PASSED, [])

    @pytest.mark.parametrize("batch_read", [False, True])
    def test_context_rejects_status_filter(self, batch_read: bool) -> None:
        row = make_observation()
        store = FakeObservationStore().qc_context_reader()
        with pytest.raises(ValueError, match="full cohort"):
            if batch_read:
                store.fetch_observations_batch(
                    [row.station_id],
                    row.parameter,
                    row.timestamp,
                    row.timestamp + timedelta(hours=1),
                    qc_status=QcStatus.RAW,
                )
            else:
                store.fetch_observations(
                    row.station_id,
                    row.parameter,
                    row.timestamp,
                    row.timestamp + timedelta(hours=1),
                    qc_status=QcStatus.RAW,
                )

    def test_partial_cohort_rejected_but_target_selection_after_check_is_supported(
        self,
    ) -> None:
        from sapphire_flow.exceptions import ConfigurationError
        from sapphire_flow.services.qc import infer_time_step

        first = make_observation(qc_status=QcStatus.RAW)
        second = make_observation(
            station_id=first.station_id,
            timestamp=first.timestamp + timedelta(hours=1),
            qc_status=QcStatus.QC_PASSED,
        )
        store = FakeObservationStore().qc_context_reader()
        store.store_observations([first, second])
        rows = store.fetch_observations(
            first.station_id,
            first.parameter,
            first.timestamp,
            second.timestamp + timedelta(hours=1),
        )
        assert infer_time_step(rows) == timedelta(hours=1)
        with pytest.raises(ConfigurationError, match="full cohort"):
            infer_time_step(rows[:1])
        assert store.update_qc_batch_if_current(
            [(rows[0], QcStatus.QC_PASSED, [])], "v2"
        )
        assert not store.update_qc_batch_if_current(
            [(rows[0], QcStatus.QC_FAILED, [])], "v1"
        )


class TestFakeObservationSnapshotWork:
    @pytest.mark.parametrize("qc_context", [False, True])
    def test_snapshot_serialization_is_linear_per_query(self, qc_context: bool) -> None:
        serializations = 0

        class CountedFloat(float):
            def __repr__(self) -> str:
                nonlocal serializations
                serializations += 1
                return super().__repr__()

        rng = random.Random(42)
        first = make_observation(value=CountedFloat(1.0), rng=rng)
        rows = [
            make_observation(
                station_id=first.station_id,
                rng=rng,
                timestamp=first.timestamp + timedelta(minutes=i),
                value=CountedFloat(1.0),
            )
            for i in range(100)
        ]
        store = FakeObservationStore()
        store.store_observations(rows)
        reader = store.qc_context_reader() if qc_context else store
        serializations = 0
        fetched = reader.fetch_observations(
            first.station_id,
            first.parameter,
            first.timestamp,
            first.timestamp + timedelta(hours=2),
        )
        assert len(fetched) == len(rows)
        assert serializations <= 2 * len(rows) if qc_context else serializations == 0
        assert all((row.qc_read_token is not None) == qc_context for row in fetched)
        if qc_context:
            serializations = 0
            assert reader.update_qc_batch_if_current(
                [(row, QcStatus.QC_PASSED, []) for row in fetched], "v2"
            )
            assert serializations <= 2 * len(rows)

    @pytest.mark.parametrize("mutation", ["value", "hold", "evidence", "qc"])
    def test_query_reuse_does_not_cache_across_mutations(self, mutation: str) -> None:
        rng = random.Random(42)
        first = make_observation(rng=rng)
        neighbour = make_observation(
            station_id=first.station_id,
            rng=rng,
            timestamp=first.timestamp + timedelta(minutes=1),
        )
        store = FakeObservationStore()
        store.store_observations([first, neighbour])
        reader = store.qc_context_reader()
        fetched = reader.fetch_observations(
            first.station_id,
            first.parameter,
            first.timestamp,
            first.timestamp + timedelta(hours=1),
        )
        if mutation == "value":
            store.store_observations([replace(neighbour, value=42.0)])
        elif mutation == "hold":
            store.hold_unit_observation(neighbour.id, "new-hold")
        elif mutation == "evidence":
            store.record_unit_evidence(neighbour)
        else:
            store.update_qc(first.id, QcStatus.QC_FAILED, [], "new-rule")
        assert not reader.update_qc_batch_if_current(
            [(fetched[0], QcStatus.QC_PASSED, [])], "old-rule"
        )

    def test_excluded_rows_split_segments_without_shrinking_context(self) -> None:
        rng = random.Random(42)
        first = make_observation(rng=rng)
        held = make_observation(
            station_id=first.station_id,
            rng=rng,
            timestamp=first.timestamp + timedelta(minutes=1),
        )
        last = make_observation(
            station_id=first.station_id,
            rng=rng,
            timestamp=first.timestamp + timedelta(minutes=2),
        )
        store = FakeObservationStore()
        store.store_observations([last, held, first])
        store.hold_unit_observation(held.id, "unverified")
        reader = store.qc_context_reader()
        fetched = reader.fetch_observations(
            first.station_id,
            first.parameter,
            first.timestamp,
            first.timestamp + timedelta(hours=1),
        )
        by_id = {row.id: row for row in fetched}
        assert set(by_id) == {first.id, last.id}
        assert by_id[first.id].qc_context_segment == 0
        assert by_id[last.id].qc_context_segment == 1
        assert all(
            row.qc_read_token is not None and row.qc_read_token.cohort_size == 2
            for row in fetched
        )
        store.release_unit_hold(held.id, "unverified")
        assert not reader.update_qc_batch_if_current(
            [(by_id[first.id], QcStatus.QC_PASSED, [])]
        )


class TestConditionalQcEntryPointParity:
    @pytest.mark.parametrize("batch", [False, True])
    @pytest.mark.parametrize(
        "invalid_token", ["trusted", "unbounded", "missing_prior_qc"]
    )
    def test_rejects_reads_without_complete_conditional_context(
        self,
        batch: bool,
        invalid_token: Literal["trusted", "unbounded", "missing_prior_qc"],
    ) -> None:
        row = make_observation()
        store = FakeObservationStore()
        store.store_observations([row])
        reader = store.qc_context_reader()
        observed = reader.fetch_observations(
            row.station_id,
            row.parameter,
            row.timestamp,
            row.timestamp + timedelta(hours=1),
        )[0]
        assert observed.qc_read_token is not None
        if invalid_token == "trusted":
            observed = store.fetch_observation_by_id(row.id)
            assert observed is not None
        elif invalid_token == "unbounded":
            observed = replace(
                observed, qc_read_token=replace(observed.qc_read_token, window=None)
            )
        else:
            observed = replace(
                observed,
                qc_read_token=replace(observed.qc_read_token, qc_state_sha256=None),
            )
        with pytest.raises(ValueError, match="bounded snapshot read token"):
            if batch:
                reader.update_qc_batch_if_current([(observed, QcStatus.QC_PASSED, [])])
            else:
                reader.update_qc_if_current(observed, QcStatus.QC_PASSED, [])
        assert store.fetch_observation_by_id(row.id) == row
