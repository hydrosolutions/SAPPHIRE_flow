# Pydantic resolves boundary annotations at runtime.
# ruff: noqa: TC001, TC003
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from sapphire_flow.api.schemas import ThresholdResponse
from sapphire_flow.store.forecast_period_codec import ForecastPeriodPayload
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
    end: datetime = Field(
        description=(
            "UTC window end. Hourly slots select the last "
            "eligible reading in (end - 1 hour, end]."
        )
    )
    availability: ObservationAvailability
    sample: ObservationSampleResponse | None = Field(
        description=(
            "Eligible reading for this slot, or null; null never means zero discharge."
        )
    )


class OverviewObservationResponse(OverviewSchema):
    parameter: Literal["water_level", "discharge"]
    unit: Literal["m", "m³/s"]
    latest: ObservationSlotResponse = Field(
        description=(
            "Latest eligible reading within the independent seven-day lookback window."
        )
    )
    latest_failed_at: datetime | None
    newer_failed: bool | None
    hourly: tuple[ObservationSlotResponse, ...] = Field(
        description=(
            "72 completed UTC hourly slots, oldest first; "
            "values are last eligible readings, not hourly "
            "means."
        )
    )


class ForecastPointResponse(OverviewSchema):
    valid_time: datetime
    median: float
    p25: float | None
    p75: float | None


class ForecastGapResponse(OverviewSchema):
    start: datetime
    end: datetime


class OverviewForecastResponse(OverviewSchema):
    period_definition: ForecastPeriodPayload | None = None
    model_id: str
    parameter: str | None
    data_use: ForecastDataUse
    availability: (
        Literal["no_forecast", "withheld_or_ineligible"] | OverviewAvailability
    )
    freshness: OverviewFreshness = Field(
        default=OverviewFreshness.UNKNOWN,
        description=(
            "Timeliness under the configured issuance policy; "
            "unknown means no applicable assessment. Separate "
            "from input quality."
        ),
    )
    forecast_id: str | None = None
    unit: str | None = None
    issued_at: datetime | None = None
    original_horizon_steps: int | None = None
    time_step_seconds: float | None = None
    expected_issue: datetime | None = None
    delivery_deadline: datetime | None = None
    qc_status: QcStatus | None = None
    input_quality: str | None = None
    input_quality_codes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Input-quality category:level pairs; retrieve "
            "detail for full flag messages."
        ),
    )
    provisional: Literal["provisional", "standard"]
    peak: ForecastPointResponse | None = None
    support_start: datetime | None = None
    support_end: datetime | None = None
    series: tuple[ForecastPointResponse, ...] = Field(
        default=(),
        description=(
            "Compact original forecast points, not a resampled "
            "or interpolated full ensemble."
        ),
    )
    gaps: tuple[ForecastGapResponse, ...] = ()
    original_point_count: int = 0
    returned_point_count: int = 0
    reduction: Literal["original_points_gap_peak_anchors"] = (
        "original_points_gap_peak_anchors"
    )
    quartile_method: QuartileMethod | None = None
    band_availability: Literal["available", "unavailable"] | None = None
    detail_url: str | None = Field(
        default=None,
        description=(
            "Relative URL for this forecast including its "
            "data-use category; use unchanged for detail "
            "retrieval."
        ),
    )


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
    generated_at: datetime = Field(
        description=(
            "Generation time of this response body, not source "
            "freshness. A 304 retains the earlier body and "
            "timestamp."
        )
    )
    latest_lookback_seconds: Literal[604800] = 604800
    forecast_lookback_seconds: Literal[604800] = 604800
    hourly_end: datetime
    hourly_step_seconds: Literal[3600] = 3600
    hourly_count: Literal[72] = 72
    network: str
    station_count: int
    items: tuple[OverviewStationResponse, ...]
