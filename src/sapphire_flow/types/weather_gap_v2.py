from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.time_grid import TimeGrid  # noqa: TC001 - evidence schema
from sapphire_flow.types.weather_gap import GAP_WEATHER_PARAMETERS, WeatherGapMethod

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.weather import WeatherForecastRecord
    from sapphire_flow.types.weather_gap import GapWeatherParameter


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherGapEndpointV2:
    start: UtcDatetime
    end: UtcDatetime
    value: float

    def __post_init__(self) -> None:
        from sapphire_flow.types.operational_weather import require_utc

        require_utc(self.start)
        require_utc(self.end)
        if self.end - self.start != timedelta(days=1) or not math.isfinite(self.value):
            raise ValueError("gap endpoint requires a finite daily period")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherGapFillV2:
    grid: TimeGrid
    parameter: GapWeatherParameter
    start: UtcDatetime
    value: float
    method: WeatherGapMethod
    endpoints: tuple[WeatherGapEndpointV2, ...] = ()
    donor_records: tuple[WeatherForecastRecord, ...] = ()

    @property
    def end(self) -> UtcDatetime:
        return ensure_utc(self.start + self.grid.step)

    def __post_init__(self) -> None:
        # Imported here because native weather samples also reference v1 gap facts.
        from sapphire_flow.types.operational_weather import (
            qualified_ifs_daily_support,
            require_daily_grid_boundary,
            require_midnight,
        )

        require_daily_grid_boundary(self.start, self.grid)
        if self.parameter not in GAP_WEATHER_PARAMETERS or not math.isfinite(
            self.value
        ):
            raise ValueError("weather fill requires a supported finite value")
        if self.parameter != "temperature" and self.value < 0:
            raise ValueError("weather fill must be nonnegative")
        if self.method is WeatherGapMethod.LINEAR:
            if len(self.endpoints) != 2 or self.donor_records:
                raise ValueError("linear fill requires two original endpoints")
            left, right = self.endpoints
            for endpoint in self.endpoints:
                require_daily_grid_boundary(endpoint.start, self.grid)
            if not left.start < self.start < right.start:
                raise ValueError("weather fill cannot extrapolate")
            if (
                self.parameter == "precipitation"
                and right.start - left.start != 2 * self.grid.step
            ):
                raise ValueError("rainfall interpolation requires a single missing day")
            expected = left.value + (right.value - left.value) * (
                (self.start - left.start) / (right.start - left.start)
            )
        elif self.method is WeatherGapMethod.STORED_FORECAST:
            if (
                self.endpoints
                or not self.donor_records
                or self.parameter == "global_radiation"
            ):
                raise ValueError(
                    "stored fill requires qualified native forecast records"
                )
            first = self.donor_records[0]
            require_midnight(first.cycle_time)
            support = qualified_ifs_daily_support(
                first.cycle_time, self.start, grid=self.grid
            )
            rows = sorted(self.donor_records, key=lambda row: row.valid_time)
            if (
                not support
                or tuple(row.valid_time for row in rows) != support
                or any(
                    row.station_id != first.station_id
                    or row.nwp_source != "ifs_ecmwf"
                    or row.cycle_time != first.cycle_time
                    or row.parameter != self.parameter
                    or row.member_id != 0
                    or row.spatial_type is not SpatialRepresentation.BASIN_AVERAGE
                    or row.band_id is not None
                    or row.is_gap
                    or row.gap_status is not None
                    or isinstance(row.value, bool)
                    or not math.isfinite(row.value)
                    or (self.parameter == "precipitation" and row.value < 0)
                    for row in rows
                )
            ):
                raise ValueError(
                    "stored donor lacks complete single-cycle physical support"
                )
            expected = math.fsum(row.value for row in rows)
            if self.parameter == "temperature":
                expected /= len(rows)
        else:
            raise ValueError("unsupported weather gap method")
        if self.value != expected:
            raise ValueError("weather fill differs from its recorded support")
