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

from sapphire_flow.adapters.hybrid_reanalysis import HybridForcingSource
from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    select_reanalysis_source,
)
from sapphire_flow.adapters.per_source_store_reader import PerSourceStoreReader
from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
from sapphire_flow.types.forcing_sources import ForcingSource
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
