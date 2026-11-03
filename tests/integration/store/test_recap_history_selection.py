from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sapphire_flow.adapters.hybrid_reanalysis_factories import select_reanalysis_source
from sapphire_flow.services.nepal_onboarding import history_binding
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceRole
from sapphire_flow.types.nepal_onboarding import GATEWAY_HISTORY_SOURCE
from tests.conftest import make_raw_historical_forcing
from tests.integration.store.test_historical_forcing_store import _seed_station

if TYPE_CHECKING:
    import sqlalchemy as sa


class TestRecapHistorySelection:
    def test_station_version_metadata_and_half_open_window(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgHistoricalForcingStore(db_connection)
        stations = [_seed_station(db_connection), _seed_station(db_connection)]
        start = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
        end = ensure_utc(start + timedelta(hours=2))
        expected = []
        for index, sid in enumerate(stations):
            row = make_raw_historical_forcing(
                station_id=sid,
                source=GATEWAY_HISTORY_SOURCE,
                version="old",
                valid_time=start,
                parameter="precipitation",
                value=1.0,
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=3,
                member_id=2,
            )
            store.store_forcing([row])
            latest = replace(row, version="new", value=7.5 + index)
            inside = replace(latest, valid_time=ensure_utc(start + timedelta(hours=1)))
            store.store_forcing(
                [
                    latest,
                    inside,
                    replace(latest, valid_time=ensure_utc(start - timedelta(hours=1))),
                    replace(latest, valid_time=end),
                    replace(latest, source="era5_land", value=99.0),
                    replace(latest, source="meteoswiss_rhiresd", value=88.0),
                ]
            )
            expected.append([latest, inside])
        reader = select_reanalysis_source(forcing_store=store, mode="recap_era5_land")
        for sid, rows in zip(stations, expected, strict=True):
            binding = history_binding(sid)
            assert binding.nwp_source == "era5_land"
            assert binding.role is WeatherSourceRole.REANALYSIS
            actual = reader.fetch_reanalysis([binding], start, end, ["precipitation"])
            assert sorted(actual, key=lambda r: r.valid_time) == rows
