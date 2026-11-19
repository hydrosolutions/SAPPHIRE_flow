from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID

import pytest

from sapphire_flow.adapters.deployment_smoke_weather import (
    DeploymentSmokeWeatherAdapter,
)
from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource

_CYCLE = ensure_utc(datetime(2025, 1, 3, tzinfo=UTC))
_CSV = files("sapphire_flow").joinpath("data/deployment_smoke/weather.csv").read_bytes()
_BINDING = StationWeatherSource(
    station_id=StationId(UUID(int=1)),
    nwp_source="icon_ch2_eps",
    extraction_type=SpatialRepresentation.POINT,
    role=WeatherSourceRole.FORECAST,
    status=WeatherSourceStatus.ACTIVE,
)


class TestDeploymentSmokeWeatherAdapter:
    def test_serves_only_the_requested_station_and_fixed_cycle(self) -> None:
        result = DeploymentSmokeWeatherAdapter(_CSV, cycle_time=_CYCLE).fetch_forecasts(
            [_BINDING], _CYCLE
        )
        assert set(result) == {_BINDING.station_id}
        forecast = result[_BINDING.station_id]
        assert forecast.cycle_time == _CYCLE
        assert forecast.values["value"].to_list() == [1.0, 2.0, 4.0]
        assert forecast.values["member_id"].to_list() == [0, 0, 0]

    def test_wrong_cycle_refuses(self) -> None:
        with pytest.raises(AdapterError, match="fixture cycle"):
            DeploymentSmokeWeatherAdapter(_CSV, cycle_time=_CYCLE).fetch_forecasts(
                [_BINDING], ensure_utc(_CYCLE + timedelta(hours=1))
            )

    @pytest.mark.parametrize(
        "bindings",
        [
            [],
            [_BINDING, _BINDING],
            [replace(_BINDING, nwp_source="other")],
            [replace(_BINDING, extraction_type=SpatialRepresentation.BASIN_AVERAGE)],
        ],
    )
    def test_unmatched_binding_refuses(
        self, bindings: list[StationWeatherSource]
    ) -> None:
        with pytest.raises(AdapterError, match="binding"):
            DeploymentSmokeWeatherAdapter(_CSV, cycle_time=_CYCLE).fetch_forecasts(
                bindings, _CYCLE
            )

    @pytest.mark.parametrize(
        "csv_bytes",
        [
            _CSV.replace(b"01:00:00Z", b"04:00:00Z"),
            _CSV.replace(b"Z,", b","),
            _CSV.replace(b",2.0", b",nan"),
            _CSV.replace(b",0,2.0", b",1,2.0"),
            b"valid_time,parameter,member_id,value\n",
        ],
    )
    def test_malformed_or_incomplete_weather_refuses(self, csv_bytes: bytes) -> None:
        with pytest.raises(ConfigurationError, match="smoke weather"):
            DeploymentSmokeWeatherAdapter(csv_bytes, cycle_time=_CYCLE)
