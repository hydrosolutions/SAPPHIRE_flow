from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation_history import HistoryRead, HistoryRowLimitError
from tests.conftest import make_observation
from tests.fakes.fake_stores import FakeObservationStore


class TestFakeObservationHistory:
    def test_eligibility_precedes_counts_and_holds_refresh(self) -> None:
        sid = StationId(UUID(int=100))
        cutoff = ensure_utc(datetime(2026, 11, 5, tzinfo=UTC))
        store = FakeObservationStore()
        rows = [
            replace(
                make_observation(
                    station_id=sid,
                    timestamp=ensure_utc(cutoff - timedelta(seconds=i)),
                    value=float(i),
                    qc_status=QcStatus.QC_PASSED,
                ),
                id=ObservationId(UUID(int=200 + i)),
                delivery_id=DELIVERY_ID if i else None,
            )
            for i in range(20)
        ]
        store.store_observations(rows)
        request = HistoryRead(
            station_ids=(sid,),
            start=ensure_utc(cutoff - timedelta(hours=1)),
            as_of=cutoff,
            excluded_delivery_id=DELIVERY_ID,
            station_row_cap=1,
            fleet_row_cap=1,
        )
        assert len(store.fetch_observation_history(request)) == 1
        store.hold_unit_observation(rows[0].id, "test-hold")
        assert store.fetch_observation_history(request) == ()
        store.release_unit_hold(rows[0].id, "test-hold")
        assert len(store.fetch_observation_history(request)) == 1
        store.store_observations(
            [
                replace(
                    rows[1],
                    id=ObservationId(UUID(int=999)),
                    timestamp=ensure_utc(cutoff - timedelta(minutes=1)),
                    delivery_id=None,
                )
            ]
        )
        with pytest.raises(HistoryRowLimitError, match="exceeds bound"):
            store.fetch_observation_history(request)
        with pytest.raises(ValueError, match="trusted"):
            store.qc_context_reader().fetch_observation_history(request)
