from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows import ingest_recap_era5_reanalysis as module
from sapphire_flow.services.nepal_onboarding import history_binding
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.nepal_onboarding import GATEWAY_HISTORY_SOURCE, HistoryWindow
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import FakeHistoricalForcingStore

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.station import StationWeatherSource

START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
WINDOW = HistoryWindow(start=START, end=ensure_utc(START + timedelta(days=70)))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)


class TestIngestRecapEra5Reanalysis:
    @pytest.mark.parametrize("invalid", [None, "source", "station", "end", "nan"])
    def test_bounds_provenance_and_missing_parameter_coverage(
        self, monkeypatch: pytest.MonkeyPatch, invalid: str | None
    ) -> None:
        station = make_station_config()
        store = FakeHistoricalForcingStore()
        monkeypatch.setattr(module, "chwrr_stations", lambda *a, **kw: [station])
        monkeypatch.setattr(
            module,
            "require_history_bindings",
            lambda *a: [history_binding(station.id)],
        )
        monkeypatch.setattr(module, "PgHistoricalForcingStore", lambda _: store)
        windows = []

        class Adapter:
            def fetch_reanalysis(
                self,
                station_configs: list[StationWeatherSource],
                start: UtcDatetime,
                end: UtcDatetime,
                parameters: list[str],
            ) -> list[RawHistoricalForcing]:
                windows.append((start, end))
                assert parameters == ["precipitation", "temperature"]
                row = make_raw_historical_forcing(
                    station_id=station.id,
                    source=GATEWAY_HISTORY_SOURCE,
                    parameter="temperature",
                    valid_time=start,
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                )
                changes = {
                    "source": {"source": "forecast_fill"},
                    "station": {"station_id": uuid4()},
                    "end": {"valid_time": end},
                    "nan": {"value": float("nan")},
                }
                return [replace(row, **changes[invalid]) if invalid else row]

        if invalid:
            with pytest.raises(ConfigurationError, match="history contract"):
                module.ingest_recap_era5_reanalysis_flow.fn(
                    None, IDENTITY, Adapter(), WINDOW, now=START
                )
            assert (
                store.fetch_forcing(
                    station.id, GATEWAY_HISTORY_SOURCE, START, WINDOW.end
                )
                == []
            )
        else:
            report = module.ingest_recap_era5_reanalysis_flow.fn(
                None, IDENTITY, Adapter(), WINDOW, now=START
            )
            assert [int((end - start).days) for start, end in windows] == [31, 31, 8]
            assert [(r.parameter, r.rows) for r in report] == [
                ("precipitation", 0),
                ("temperature", 3),
            ]
