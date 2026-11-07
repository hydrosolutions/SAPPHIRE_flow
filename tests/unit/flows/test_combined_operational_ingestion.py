from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import TenantIsolationError
from sapphire_flow.flows.ingest_recap_era5_reanalysis import (
    fetch_combined_operational_windows,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.operational_weather import OperationalWeatherWindow
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore
from tests.fakes.operational_weather import ISSUE, make_operational_window
from tests.unit.adapters.test_recap_operational import make_binding


class RecordingOperationalSource:
    def __init__(self) -> None:
        self.calls: list[tuple[StationId, ...]] = []

    def fetch_windows(
        self,
        *,
        sources: list[StationWeatherSource],
        issue_time: UtcDatetime,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> dict[StationId, OperationalWeatherWindow]:
        self.calls.append(tuple(source.station_id for source in sources))
        return {
            source.station_id: make_operational_window(source.station_id)
            for source in sources
        }


class TestCombinedOperationalIngestion:
    def test_provider_receives_one_batch_after_canonical_authorization(self) -> None:
        source = make_binding()
        station = make_station_config(station_id=source.station_id)
        stations = FakeStationStore()
        stations.store_station(station)
        adapter = RecordingOperationalSource()
        result = fetch_combined_operational_windows(
            adapter=adapter,
            sources=[source],
            station_store=stations,
            principal=WritePrincipal(id=None, tenant_id=station.tenant_id),
            audit_log_store=None,
            issue_time=ISSUE,
            start=ensure_utc(ISSUE - timedelta(days=1)),
            end=ISSUE,
            now=ISSUE,
        )
        assert tuple(result) == (station.id,)
        assert adapter.calls == [(station.id,)]

    def test_later_cross_tenant_source_blocks_entire_batch_before_provider(
        self,
    ) -> None:
        first = make_binding()
        second = replace(first, station_id=StationId(uuid4()))
        station = make_station_config(station_id=first.station_id)
        other = make_station_config(
            station_id=second.station_id, tenant_id=TenantId(uuid4())
        )
        stations = FakeStationStore()
        stations.store_station(station)
        stations.store_station(other)
        adapter = RecordingOperationalSource()
        with pytest.raises(TenantIsolationError, match="not authorized"):
            fetch_combined_operational_windows(
                adapter=adapter,
                sources=[first, second],
                station_store=stations,
                principal=WritePrincipal(id=None, tenant_id=station.tenant_id),
                audit_log_store=None,
                issue_time=ISSUE,
                start=ensure_utc(ISSUE - timedelta(days=1)),
                end=ISSUE,
                now=ISSUE,
            )
        assert adapter.calls == []
