from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from sapphire_flow.exceptions import TenantIsolationError
from sapphire_flow.types.ids import StationId, TenantId
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401
    _FakeCandidateAwareSource,
)
from tests.unit.flows.test_selected_weather_cycle import (
    FixtureOperationalApi,
    _run_selected_cycle,
)

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.ids import ModelId
    from tests.fakes.fake_stores import FakeStationGroupStore


def test_selected_native_cross_tenant_refresh_refuses_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_provider(*args: object, **kwargs: object) -> object:
        pytest.fail("cross-tenant refresh reached provider")

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        station = stores["station_store"].fetch_station(StationId(UUID(int=2)))
        assert station is not None
        stores["station_store"].store_station(
            replace(station, tenant_id=TenantId(uuid4()))
        )
        return {}

    monkeypatch.setattr(FixtureOperationalApi, "operational", forbidden_provider)
    monkeypatch.setattr(
        _FakeCandidateAwareSource, "fetch_requirement", forbidden_provider
    )
    with pytest.raises(TenantIsolationError, match="tenant"):
        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            station_count=2,
            configure_cycle=configure,
        )
