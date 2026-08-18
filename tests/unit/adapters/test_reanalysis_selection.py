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

import uuid
from datetime import UTC, datetime

import numpy as np
import xarray as xr
from shapely.geometry import box

from sapphire_flow.adapters.era5_land_reanalysis import Era5LandReanalysisAdapter
from sapphire_flow.adapters.hybrid_reanalysis import HybridForcingSource
from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    select_reanalysis_source,
)
from sapphire_flow.adapters.per_source_store_reader import PerSourceStoreReader
from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
    ExactExtractGridExtractor,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import BasinId
from sapphire_flow.types.station import StationWeatherSource
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeHistoricalForcingStore

_EPOCH = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


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
        """R2-3 fix (second fixer round, 2026-08-18): the private-attribute
        assertion above would pass even if the selected reader returned zero
        rows for a real store — the exact silent-failure mode this plan
        exists to close (writing ``mean_temperature`` instead of
        ``temperature`` would fail exactly this way). The PREVIOUS version of
        this test stored hardcoded ``parameter="precipitation"``/
        ``"temperature"`` rows directly via ``forcing_store.store_forcing``,
        so ``Era5LandReanalysisAdapter.fetch_reanalysis`` — the code that
        actually performs the AQUAIRE -> SAP3-canonical rename
        (``mean_temperature`` -> ``temperature``) — never ran; a regression
        reintroducing ``parameter="mean_temperature"`` there would NOT have
        been caught. This version drives the round-trip through the real
        adapter: WRITE via ``Era5LandReanalysisAdapter.fetch_reanalysis`` +
        ``forcing_store.store_forcing``, then READ via the selected reader,
        and assert BOTH canonical parameters actually come back."""
        forcing_store = FakeHistoricalForcingStore()
        basin = Basin(
            id=BasinId(uuid.uuid4()),
            code="test-basin",
            name="Test basin",
            geometry=box(6.0, 46.0, 10.0, 48.0),
            area_km2=100.0,
            attributes=None,
            band_geometries=None,
            created_at=_EPOCH,
            network="bafu",
        )
        station = make_station_config(basin_id=basin.id)
        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2020, 1, 3, tzinfo=UTC))
        binding = StationWeatherSource(
            station_id=station.id,
            nwp_source=ForcingSource.ERA5_LAND.value,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )

        def _open_store(path: str) -> xr.Dataset:
            variable = (
                "temperature_2m_mean"
                if "temperature" in path
                else ("total_precipitation_sum")
            )
            fill_value = 280.65 if variable == "temperature_2m_mean" else 0.003
            return xr.Dataset(
                {
                    variable: xr.DataArray(
                        np.full((2, 4, 4), fill_value, dtype=np.float64),
                        dims=["time", "latitude", "longitude"],
                        coords={
                            "time": [datetime(2020, 1, 1), datetime(2020, 1, 2)],
                            "latitude": np.linspace(46.0, 48.0, 4),
                            "longitude": np.linspace(6.0, 10.0, 4),
                        },
                    )
                }
            )

        adapter = Era5LandReanalysisAdapter(
            extractor=ExactExtractGridExtractor(),
            basins={station.id: basin},
            clock=lambda: _EPOCH,
            open_store=_open_store,
        )
        written_rows = adapter.fetch_reanalysis(
            [binding], start, end, ["precipitation", "temperature"]
        )
        assert written_rows, "adapter produced no rows to write — fixture is wrong"
        forcing_store.store_forcing(written_rows)

        source = select_reanalysis_source(forcing_store=forcing_store, mode="era5_land")
        rows = source.fetch_reanalysis(
            [binding], start, end, ["precipitation", "temperature"]
        )

        assert {r.parameter for r in rows} == {"precipitation", "temperature"}
        assert "mean_temperature" not in {r.parameter for r in rows}
