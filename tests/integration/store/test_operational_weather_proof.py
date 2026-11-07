from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.services.operational_weather_proof import (
    WeatherProofError,
    verify_future_receipt,
)
from sapphire_flow.services.track_resolution import AcceptedCandidate, commit_track
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.weather_forecast_store import PgWeatherForecastStore
from sapphire_flow.types.enums import NwpCycleSource
from sapphire_flow.types.forcing_track import StationTrackAvailable, TrackFetchResult
from tests.conftest import make_station_config
from tests.fakes.operational_weather import ISSUE
from tests.unit.services.test_operational_weather_proof import proof_rows

if TYPE_CHECKING:
    import sqlalchemy as sa


class TestSelectedTrackArchiveProof:
    def test_natural_key_retry_preserves_real_ids_and_refuses_changed_values(
        self,
        db_connection: sa.Connection,
    ) -> None:
        scope, rows = proof_rows()
        PgStationStore(db_connection).store_station(
            make_station_config(station_id=scope.station_id)
        )
        store = PgWeatherForecastStore(db_connection)
        candidate = AcceptedCandidate(
            resolved_cycle=ISSUE,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            complete_station_records={scope.station_id: rows},
            incomplete_at_cycle=frozenset(),
            missing_polygon_column=frozenset(),
        )

        def commit(accepted: AcceptedCandidate) -> TrackFetchResult:
            return commit_track(
                accepted,
                in_scope_station_ids=frozenset({scope.station_id}),
                weather_forecast_store=store,
                nwp_source="ifs",
                track_features=frozenset({"precipitation"}),
                receipt_scopes={scope.station_id: (scope,)},
                receipt_validated_at=ISSUE,
            )

        first = commit(candidate)
        retry_rows = [replace(row, id=uuid4()) for row in rows]
        retry = commit(
            replace(candidate, complete_station_records={scope.station_id: retry_rows})
        )
        assert first.weather_receipts == retry.weather_receipts
        actual = retry.station_outcomes[scope.station_id]
        assert isinstance(actual, StationTrackAvailable)
        assert {row.id for row in actual.records} == {row.id for row in rows}
        verify_future_receipt(first.weather_receipts[0], actual.records)
        changed = [replace(retry_rows[0], id=uuid4(), value=99.0), retry_rows[1]]
        with pytest.raises(WeatherProofError, match="differs from fetched"):
            commit(
                replace(candidate, complete_station_records={scope.station_id: changed})
            )
