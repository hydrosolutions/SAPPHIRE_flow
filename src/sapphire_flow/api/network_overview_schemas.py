# Pydantic resolves boundary annotations at runtime.
# ruff: noqa: TC001, TC003
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, FiniteFloat

from sapphire_flow.api.schemas import ThresholdResponse
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.network_overview import (
    ObservationAvailability,
    OverviewAvailability,
    OverviewFreshness,
    QuartileMethod,
)


class OverviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationSampleResponse(OverviewSchema):
    timestamp: datetime
    value: float
    source: Literal["measured", "supplied"]
    qc_status: QcStatus


class ObservationSlotResponse(OverviewSchema):
    end: datetime
    availability: ObservationAvailability
    sample: ObservationSampleResponse | None


class OverviewObservationResponse(OverviewSchema):
    parameter: Literal["water_level", "discharge"]
    unit: Literal["m", "m³/s"]
    latest: ObservationSlotResponse
    latest_failed_at: datetime | None
    newer_failed: bool | None
    hourly: tuple[ObservationSlotResponse, ...]


class ForecastPointResponse(OverviewSchema):
    valid_time: datetime
    median: float
    p25: float | None
    p75: float | None


class ForecastGapResponse(OverviewSchema):
    start: datetime
    end: datetime


class OverviewForecastResponse(OverviewSchema):
    model_id: str
    parameter: str | None
    data_use: ForecastDataUse
    availability: (
        Literal["no_forecast", "withheld_or_ineligible"] | OverviewAvailability
    )
    freshness: OverviewFreshness = OverviewFreshness.UNKNOWN
    forecast_id: str | None = None
    unit: str | None = None
    issued_at: datetime | None = None
    original_horizon_steps: int | None = None
    time_step_seconds: float | None = None
    expected_issue: datetime | None = None
    delivery_deadline: datetime | None = None
    qc_status: QcStatus | None = None
    input_quality: str | None = None
    input_quality_codes: tuple[str, ...] = ()
    provisional: Literal["provisional", "standard"]
    peak: ForecastPointResponse | None = None
    support_start: datetime | None = None
    support_end: datetime | None = None
    series: tuple[ForecastPointResponse, ...] = ()
    gaps: tuple[ForecastGapResponse, ...] = ()
    original_point_count: int = 0
    returned_point_count: int = 0
    reduction: Literal["original_points_gap_peak_anchors"] = (
        "original_points_gap_peak_anchors"
    )
    quartile_method: QuartileMethod | None = None
    band_availability: Literal["available", "unavailable"] | None = None
    detail_url: str | None = None


class DisclosedWaterLevelDatumResponse(OverviewSchema):
    availability: Literal["disclosed"] = "disclosed"
    value_masl: FiniteFloat | None


class UndisclosedWaterLevelDatumResponse(OverviewSchema):
    availability: Literal["not_disclosed"] = "not_disclosed"


class OverviewStationResponse(OverviewSchema):
    station_id: str
    code: str
    name: str
    network: str
    latitude: float
    longitude: float
    measured_parameters: tuple[str, ...]
    water_level_datum: (
        DisclosedWaterLevelDatumResponse | UndisclosedWaterLevelDatumResponse
    )
    thresholds: tuple[ThresholdResponse, ...]
    water_level: OverviewObservationResponse
    discharge: OverviewObservationResponse
    forecasts: tuple[OverviewForecastResponse, ...]
    forecast_availability: Literal["no_forecast", "outputs"]


class NetworkOverviewResponse(OverviewSchema):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    latest_lookback_seconds: Literal[604800] = 604800
    forecast_lookback_seconds: Literal[604800] = 604800
    hourly_end: datetime
    hourly_step_seconds: Literal[3600] = 3600
    hourly_count: Literal[72] = 72
    network: str
    station_count: int
    items: tuple[OverviewStationResponse, ...]
