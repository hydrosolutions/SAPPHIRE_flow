"""Plan 072 T4 — read-side reanalysis-source selection (flow-wiring helper).

``select_reanalysis_source`` is the shared selector both the hindcast and
forecast-cycle flows call to honour ``DeploymentConfig.reanalysis_source``.
"""

from __future__ import annotations

from sapphire_flow.adapters.hybrid_reanalysis import HybridForcingSource
from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    select_reanalysis_source,
)
from sapphire_flow.adapters.store_backed_reanalysis import StoreBackedReanalysisSource
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
