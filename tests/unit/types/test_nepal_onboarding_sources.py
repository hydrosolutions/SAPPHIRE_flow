from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from sapphire_flow.adapters.recap_gateway import RecapGatewayForecastAdapter
from sapphire_flow.config.operational_weather import OperationalWeatherConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.nepal_onboarding import (
    _bindings_to_add,
    forecast_binding,
    history_binding,
    operational_binding,
    weather_bindings,
)
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.nepal_onboarding import FORECAST_SOURCE, OPERATIONAL_SOURCE

SID = StationId(uuid4())


class TestSourceNames:
    def test_forecast_source_matches_adapter(self) -> None:
        assert FORECAST_SOURCE == RecapGatewayForecastAdapter.NWP_SOURCE

    def test_operational_source_matches_config(self) -> None:
        assert OperationalWeatherConfig().source == OPERATIONAL_SOURCE


class TestWeatherBindings:
    def test_three_active_basin_average_roles(self) -> None:
        bindings = weather_bindings(SID)
        assert [b.role for b in bindings] == [
            WeatherSourceRole.REANALYSIS,
            WeatherSourceRole.FORECAST,
            WeatherSourceRole.OPERATIONAL,
        ]
        assert all(
            b.extraction_type is SpatialRepresentation.BASIN_AVERAGE for b in bindings
        )
        assert all(b.status is WeatherSourceStatus.ACTIVE for b in bindings)


class TestBindingsToAdd:
    def test_empty_station_gets_all_three(self) -> None:
        assert _bindings_to_add(SID, []) == list(weather_bindings(SID))

    def test_station_missing_only_forecast_gets_forecast_only(self) -> None:
        existing = [history_binding(SID), operational_binding(SID)]
        assert _bindings_to_add(SID, existing) == [forecast_binding(SID)]

    def test_complete_station_adds_nothing(self) -> None:
        assert _bindings_to_add(SID, list(weather_bindings(SID))) == []

    def test_inactive_row_is_refused(self) -> None:
        inactive = replace(
            operational_binding(SID), status=WeatherSourceStatus.INACTIVE
        )
        with pytest.raises(ConfigurationError, match="conflicting operational"):
            _bindings_to_add(SID, [inactive])

    def test_role_mismatch_is_refused(self) -> None:
        wrong = replace(forecast_binding(SID), role=WeatherSourceRole.OPERATIONAL)
        with pytest.raises(ConfigurationError, match="conflicting forecast"):
            _bindings_to_add(SID, [wrong])

    def test_other_forecast_source_is_refused(self) -> None:
        other = replace(forecast_binding(SID), nwp_source="icon_ch2_eps")
        with pytest.raises(ConfigurationError, match="icon_ch2_eps"):
            _bindings_to_add(SID, [other])
