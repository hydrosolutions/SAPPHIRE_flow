"""Plan 072 T4 / Plan 183 T4 — read-side reanalysis-source selection
(flow-wiring helper).

``select_reanalysis_source`` is the shared selector both the hindcast and
forecast-cycle flows call to honour ``DeploymentConfig.reanalysis_source``.
Plan 183 T4 adds the ``"era5_land"`` mode: training/hindcast/operational
paths select ERA5-Land forcing read directly from the `sloth-dynamic` store
WITHOUT touching call sites, exactly like ``"single"``/``"hybrid"`` already
do. Distinct from the Data-Gateway-mediated ERA5-Land path
`architecture-context.md` describes for production Nepal ingest.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.adapters.hybrid_reanalysis import HybridForcingSource
from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    select_reanalysis_source,
)
from sapphire_flow.adapters.per_source_store_reader import PerSourceStoreReader
from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import FakeHistoricalForcingStore


class TestSelectReanalysisSource:
    def test_single_mode_returns_store_backed_reader(self) -> None:
        source = select_reanalysis_source(
            forcing_store=FakeHistoricalForcingStore(), mode="single"
        )

        assert isinstance(source, StoreBackedReanalysisSource)

    def test_hybrid_mode_returns_hybrid_resolver(self) -> None:
        source = select_reanalysis_source(
            forcing_store=FakeHistoricalForcingStore(), mode="hybrid"
        )

        assert isinstance(source, HybridForcingSource)

    def test_era5_land_mode_returns_per_source_reader_scoped_to_era5_land(
        self,
    ) -> None:
        source = select_reanalysis_source(
            forcing_store=FakeHistoricalForcingStore(), mode="era5_land"
        )

        assert isinstance(source, PerSourceStoreReader)
        assert source._source is ForcingSource.ERA5_LAND  # noqa: SLF001

    def test_era5_land_mode_reader_returns_both_parameters_end_to_end(self) -> None:
        """Minor (fixer round): the private-attribute assertion above would
        pass even if the selected reader returned zero rows for a real store
        — the exact silent-failure mode this plan exists to close (writing
        ``mean_temperature`` instead of ``temperature`` would fail exactly
        this way). Round-trip a writer -> store -> selected-reader read and
        assert BOTH canonical parameters actually come back."""
        forcing_store = FakeHistoricalForcingStore()
        station = make_station_config()
        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2020, 1, 3, tzinfo=UTC))
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="precipitation",
                    valid_time=datetime(2020, 1, 1, tzinfo=UTC),
                    value=3.0,
                ),
                make_raw_historical_forcing(
                    station_id=station.id,
                    source=ForcingSource.ERA5_LAND.value,
                    parameter="temperature",
                    valid_time=datetime(2020, 1, 1, tzinfo=UTC),
                    value=7.5,
                ),
            ]
        )
        binding = StationWeatherSource(
            station_id=station.id,
            nwp_source=ForcingSource.ERA5_LAND.value,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )

        source = select_reanalysis_source(forcing_store=forcing_store, mode="era5_land")
        rows = source.fetch_reanalysis(
            [binding], start, end, ["precipitation", "temperature"]
        )

        assert {r.parameter for r in rows} == {"precipitation", "temperature"}
        assert "mean_temperature" not in {r.parameter for r in rows}
