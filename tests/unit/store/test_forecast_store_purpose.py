from __future__ import annotations

from typing import Any

import pytest

from sapphire_flow.protocols.stores import ForecastStore, RejectedForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from tests.fakes.fake_stores import FakeForecastStore, FakeRejectedForecastStore


class TestStorePurpose:
    @pytest.mark.parametrize(
        "store_type",
        [
            PgForecastStore,
            PgRejectedForecastStore,
            FakeForecastStore,
            FakeRejectedForecastStore,
        ],
    )
    @pytest.mark.parametrize("purpose", list(ForecastDataUse))
    def test_public_read_only_purpose(
        self, store_type: Any, purpose: ForecastDataUse
    ) -> None:
        if store_type is PgForecastStore:
            store = store_type(None, transaction_factory=lambda: None, data_use=purpose)
        elif store_type is PgRejectedForecastStore:
            store = store_type(None, transaction_factory=None, data_use=purpose)
        else:
            store = store_type(data_use=purpose)
        assert store.data_use is purpose
        protocol = (
            RejectedForecastStore
            if store_type in (PgRejectedForecastStore, FakeRejectedForecastStore)
            else ForecastStore
        )
        assert isinstance(store, protocol)
        with pytest.raises(AttributeError, match="data_use"):
            store.data_use = ForecastDataUse.STANDARD

    def test_fake_forecast_refuses_untyped_purpose(self) -> None:
        with pytest.raises(ValueError, match="typed"):
            FakeForecastStore(data_use="standard")  # type: ignore[arg-type]
