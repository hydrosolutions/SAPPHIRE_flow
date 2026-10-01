from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import DeliveryCollisionError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId
from sapphire_flow.types.observation import Observation, RawObservation
from tests.fakes.fake_stores import FakeObservationStore

_NOW = ensure_utc(datetime(2026, 10, 1, tzinfo=UTC))


def _station() -> StationId:
    return StationId(uuid4())


def _raw(
    station_id: StationId,
    *,
    value: float = 1.0,
    minutes: int = 0,
    parameter: str = "discharge",
    source: ObservationSource = ObservationSource.MANUAL_IMPORT,
    delivery_id: str | None = None,
    rating_curve_id: RatingCurveId | None = None,
    rating_curve_correction_version: str | None = None,
) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(_NOW + timedelta(minutes=minutes)),
        parameter=parameter,
        value=value,
        source=source,
        rating_curve_id=rating_curve_id,
        rating_curve_correction_version=rating_curve_correction_version,
        delivery_id=delivery_id,
    )


def _observation(
    station_id: StationId,
    *,
    value: float = 1.0,
    minutes: int = 0,
    observation_id: ObservationId | None = None,
    delivery_id: str | None = None,
    qc_status: QcStatus = QcStatus.RAW,
) -> Observation:
    return Observation(
        id=observation_id or ObservationId(uuid4()),
        station_id=station_id,
        timestamp=ensure_utc(_NOW + timedelta(minutes=minutes)),
        parameter="discharge",
        value=value,
        source=ObservationSource.MANUAL_IMPORT,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=qc_status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_NOW,
        delivery_id=delivery_id,
    )


class TestFakeObservationStoreBatchIndexContracts:
    def test_collision_refuses_entire_batch_before_mutating_any_row(self) -> None:
        station_a = _station()
        station_b = _station()
        store = FakeObservationStore()
        [original_id] = store.store_raw_observations(
            [_raw(station_a, value=10.0, delivery_id="delivery-a")]
        )

        with pytest.raises(DeliveryCollisionError, match="delivery collision"):
            store.store_raw_observations(
                [
                    _raw(station_b, value=20.0, minutes=1),
                    _raw(station_a, value=30.0, delivery_id="delivery-b"),
                ]
            )

        rows = store.observations()
        assert len(rows) == 1
        assert rows[0].id == original_id
        assert rows[0].value == 10.0
        assert rows[0].delivery_id == "delivery-a"

    def test_store_observations_collision_refuses_entire_batch(self) -> None:
        station_a = _station()
        station_b = _station()
        store = FakeObservationStore()
        existing = _observation(station_a, value=10.0, delivery_id="delivery-a")
        store.store_observations([existing])

        with pytest.raises(DeliveryCollisionError, match="delivery collision"):
            store.store_observations(
                [
                    _observation(station_b, value=20.0, minutes=1),
                    _observation(station_a, value=30.0, delivery_id="delivery-b"),
                ]
            )

        rows = store.observations()
        assert len(rows) == 1
        assert rows[0].id == existing.id
        assert rows[0].value == 10.0
        assert rows[0].delivery_id == "delivery-a"

    def test_same_batch_duplicate_raw_uses_last_raw_value_with_stable_id(self) -> None:
        station_id = _station()
        store = FakeObservationStore()

        ids = store.store_raw_observations(
            [_raw(station_id, value=1.0), _raw(station_id, value=2.0)]
        )

        assert len(ids) == 1
        [row] = store.observations()
        assert row.id == ids[0]
        assert row.value == 2.0

    def test_raw_upsert_resets_qc_and_updates_rating_provenance(self) -> None:
        station_id = _station()
        store = FakeObservationStore()
        [obs_id] = store.store_raw_observations([_raw(station_id, value=1.0)])
        flag = QcFlag(
            rule_id="range_check",
            rule_version="1.0",
            status=QcStatus.QC_PASSED,
            detail=None,
        )
        store.update_qc(obs_id, QcStatus.QC_PASSED, [flag], "rules-v1")
        curve_id = RatingCurveId(uuid4())

        ids = store.store_raw_observations(
            [
                _raw(
                    station_id,
                    value=3.0,
                    rating_curve_id=curve_id,
                    rating_curve_correction_version="curve-v2",
                )
            ]
        )

        assert ids == [obs_id]
        [row] = store.observations()
        assert row.id == obs_id
        assert row.value == 3.0
        assert row.rating_curve_id == curve_id
        assert row.rating_curve_correction_version == "curve-v2"
        assert row.qc_status is QcStatus.RAW
        assert row.qc_flags == []
        assert row.qc_rule_version is None

    def test_delete_and_reinsert_uses_a_fresh_id_without_stale_lookup(self) -> None:
        station_id = _station()
        store = FakeObservationStore()
        [deleted_id] = store.store_raw_observations(
            [_raw(station_id, value=1.0, delivery_id="delivery-a")]
        )
        assert store.delete_delivery_observations("delivery-a", [station_id]) == 1

        [new_id] = store.store_raw_observations(
            [_raw(station_id, value=2.0, delivery_id="delivery-a")]
        )

        assert new_id != deleted_id
        [row] = store.observations()
        assert row.id == new_id
        assert row.value == 2.0

    def test_store_observations_preserves_first_match_for_direct_duplicate_seed(
        self,
    ) -> None:
        station_id = _station()
        first = _observation(station_id, value=1.0)
        duplicate = replace(first, id=ObservationId(uuid4()), value=2.0)
        store = FakeObservationStore()
        store._observations[first.id] = first
        store._observations[duplicate.id] = duplicate

        replacement = _observation(
            station_id, value=3.0, observation_id=ObservationId(uuid4())
        )
        store.store_observations([replacement])

        by_id = {row.id: row for row in store.observations()}
        assert by_id[first.id].value == 3.0
        assert by_id[duplicate.id].value == 2.0
        assert replacement.id not in by_id

    def test_repeated_id_key_change_does_not_leave_stale_batch_lookup(self) -> None:
        station_id = _station()
        other_station = _station()
        reused_id = ObservationId(uuid4())
        original = _observation(station_id, value=1.0, observation_id=reused_id)
        moved = _observation(
            other_station,
            value=2.0,
            minutes=1,
            observation_id=reused_id,
        )
        replacement_for_old_key = _observation(
            station_id, value=3.0, observation_id=ObservationId(uuid4())
        )
        store = FakeObservationStore()
        store.store_observations([original])

        store.store_observations([moved, replacement_for_old_key])

        rows = sorted(store.observations(), key=lambda row: row.value or 0.0)
        assert [(row.id, row.station_id, row.value) for row in rows] == [
            (reused_id, other_station, 2.0),
            (replacement_for_old_key.id, station_id, 3.0),
        ]
