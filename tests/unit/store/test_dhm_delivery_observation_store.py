from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import DeliveryCollisionError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.observation import RawObservation
from tests.fakes.fake_stores import FakeObservationStore


def _raw(
    station_id: StationId, value: float, delivery_id: str | None
) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        parameter="discharge",
        value=value,
        source=ObservationSource.MANUAL_IMPORT,
        delivery_id=delivery_id,
    )


def test_foreign_delivery_collision_refuses_entire_batch() -> None:
    station_a = StationId(uuid4())
    station_b = StationId(uuid4())
    store = FakeObservationStore()
    store.store_raw_observations([_raw(station_a, 1.0, "delivery-a")])
    with pytest.raises(DeliveryCollisionError, match="delivery collision"):
        store.store_raw_observations(
            [_raw(station_b, 2.0, None), _raw(station_a, 3.0, "delivery-b")]
        )
    assert len(store.observations()) == 1
    assert store.observations()[0].value == 1.0


def test_null_delivery_matches_null_for_corrections() -> None:
    station_id = StationId(uuid4())
    store = FakeObservationStore()
    store.store_raw_observations([_raw(station_id, 1.0, None)])
    store.store_raw_observations([_raw(station_id, 2.0, None)])
    assert len(store.observations()) == 1
    assert store.observations()[0].value == 2.0
    assert store.observations()[0].delivery_id is None


def test_delivery_delete_preserves_unrelated_row() -> None:
    station_a = StationId(uuid4())
    station_b = StationId(uuid4())
    store = FakeObservationStore()
    store.store_raw_observations(
        [_raw(station_a, 1.0, "delivery-a"), _raw(station_b, 2.0, None)]
    )
    assert store.delete_delivery_observations("delivery-a", [station_a]) == 1
    assert store.fetch_delivery_observations("delivery-a", [station_a]) == []
    assert len(store.observations()) == 1
    assert store.observations()[0].station_id == station_b
