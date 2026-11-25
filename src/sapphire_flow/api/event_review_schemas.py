# ruff: noqa: TC001 -- Pydantic resolves boundary annotations.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sapphire_flow.types.enums import ForcingType, QcStatus, ThresholdSource
from sapphire_flow.types.event_review import (
    MAX_REVIEW_SOURCES,
    ReviewAvailability,
    ReviewCompleteness,
    ReviewErrorCode,
    ReviewKind,
    ReviewOptions,
    ReviewPointState,
    ReviewSeries,
    ReviewSourceMode,
)
from sapphire_flow.types.ids import CLIMATOLOGY_FALLBACK_MODEL_ID
from sapphire_flow.types.probability import ProbabilityMethod

LEAD_DEFINITION = (
    "Lead is the elapsed time from the forecast issue anchor to the valid timestamp. "
    "A 48-hour lead uses the issue anchored exactly 48 hours earlier. "
    "Hindcast issue anchors are retrospective, not forecasts published then."
)


class ReviewSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ReviewKind
    model_id: str
    model_name: str
    model_role: Literal["model", "baseline"]
    artifact_id: UUID | None
    artifact_sha256: str | None
    model_version: str | None = Field(
        default=None,
        description="Recorded model version, otherwise null; not a forecast revision.",
    )
    training_period_start: datetime | None
    training_period_end: datetime | None
    hindcast_run_id: UUID | None
    forcing_type: ForcingType | None = Field(
        description="Null means unknown, not absence of weather forcing."
    )
    time_step_seconds: int | None = Field(
        gt=0,
        description=(
            "Recorded forecast timestep, or null; "
            "not issue cadence or interval support."
        ),
    )
    units: str
    lead_hours: tuple[int, ...] = Field(
        description="Exact nonnegative integer leads stored; not the model horizon."
    )
    first_valid_time: datetime | None
    last_valid_time: datetime | None = Field(
        description="Inclusive last valid time; bounds do not promise full coverage."
    )
    temporal_support: Literal["unknown"] = "unknown"
    completeness: ReviewCompleteness
    reasons: tuple[str, ...]


class ReviewOptionsResponse(BaseModel):
    station_id: UUID
    parameter: Literal["discharge"] = "discharge"
    data_use: Literal["standard"] = "standard"
    lead_definition: str = LEAD_DEFINITION
    operational_availability: ReviewAvailability
    hindcast_availability: ReviewAvailability
    message: str | None
    sources: tuple[ReviewSourceResponse, ...] = Field(max_length=MAX_REVIEW_SOURCES)

    @classmethod
    def from_domain(cls, options: ReviewOptions) -> ReviewOptionsResponse:
        return cls(
            station_id=options.station_id,
            operational_availability=options.operational_availability,
            hindcast_availability=options.hindcast_availability,
            message=(
                "Hindcast review is unavailable under the publication policy."
                if options.hindcast_availability is ReviewAvailability.UNSUPPORTED
                else "No stored discharge forecasts or hindcasts for this station."
                if not options.sources
                else None
            ),
            sources=tuple(
                ReviewSourceResponse(
                    **asdict(source),
                    model_role="baseline"
                    if source.model_id == CLIMATOLOGY_FALLBACK_MODEL_ID
                    else "model",
                )
                for source in options.sources
            ),
        )


class ReviewErrorDetail(BaseModel):
    code: ReviewErrorCode
    message: str


class ReviewErrorResponse(BaseModel):
    detail: ReviewErrorDetail


class ReviewSeriesParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=200)
    lead_hours: int = Field(ge=0, le=8784)
    start: AwareDatetime
    end: AwareDatetime
    source_mode: ReviewSourceMode
    hindcast_run_id: UUID | None = None
    hindcast_artifact_id: UUID | None = None
    forcing_type: ForcingType | None = None
    time_step_seconds: int | None = Field(default=None, gt=0)


class ExceedanceProbabilityResponse(BaseModel):
    probability: float | None
    lower: float | None
    upper: float | None
    method: ProbabilityMethod
    reason: str | None


class ReviewThresholdResponse(BaseModel):
    key: str
    danger_level: str
    value: float
    source: ThresholdSource
    updated_at: datetime
    parameter: Literal["discharge"]
    units: Literal["m3/s"]


class ReviewPointResponse(BaseModel):
    valid_time: datetime
    issued_at: datetime
    forecast_id: UUID | None
    stored_at: datetime | None = Field(
        description="First storage time, not generation or publication time."
    )
    state: ReviewPointState
    reason: str | None
    qc_status: QcStatus | None
    median: float | None
    p25: float | None
    p75: float | None
    quartile_method: Literal["stored_quantiles", "member_empirical_type7"] | None
    band_reason: str | None
    threshold_probabilities: dict[str, ExceedanceProbabilityResponse]
    valid_interval_start: None = None
    valid_interval_end: None = None


class ReviewGapResponse(BaseModel):
    after: datetime
    before: datetime
    reason: str


class ReviewSegmentResponse(BaseModel):
    source: ReviewSourceResponse
    points: tuple[ReviewPointResponse, ...]
    gaps: tuple[ReviewGapResponse, ...] = Field(
        description=(
            "Empty while expected issue cadence is unknown; no schedule is inferred."
        )
    )
    generation_time: None = None


class ReviewSeriesResponse(BaseModel):
    station_id: UUID
    model_id: str
    lead_hours: int
    start: datetime
    end: datetime
    source_mode: ReviewSourceMode
    parameter: Literal["discharge"] = "discharge"
    data_use: Literal["standard"] = "standard"
    lead_definition: str = LEAD_DEFINITION
    state: ReviewPointState
    reason: str | None
    temporal_support: Literal["unknown"] = "unknown"
    support_message: str = (
        "Timestamped values; interval support is unknown. "
        "Do not score or automatically combine sources."
    )
    segments: tuple[ReviewSegmentResponse, ...]
    thresholds: tuple[ReviewThresholdResponse, ...]
    probability_definition: str = (
        "P(forecast value > threshold), compared as stored without tolerance. "
        "Per valid time, not a peak or window-event probability. Quantile point "
        "estimates assume a piecewise-linear inverse CDF within supplied support; "
        "bounds do not invent tail probabilities. Temporal support remains unknown. "
        "qc_unchecked applies to probabilities as well as plotted values."
    )
    threshold_basis: str = (
        "Current stored thresholds; historical effective dates unknown. "
        "An empty thresholds list means thresholds not configured."
    )

    @classmethod
    def from_domain(cls, series: ReviewSeries) -> ReviewSeriesResponse:
        query = series.query
        return cls(
            station_id=query.scope.station_id,
            model_id=query.model_id,
            lead_hours=query.lead_hours,
            start=query.start,
            end=query.end,
            source_mode=query.source_mode,
            thresholds=tuple(
                ReviewThresholdResponse(**asdict(t)) for t in series.thresholds
            ),
            state=series.state,
            reason=series.reason,
            segments=tuple(
                ReviewSegmentResponse(
                    source=ReviewSourceResponse(
                        **asdict(segment.source),
                        model_role="baseline"
                        if segment.source.model_id == CLIMATOLOGY_FALLBACK_MODEL_ID
                        else "model",
                    ),
                    points=tuple(
                        ReviewPointResponse(**asdict(point)) for point in segment.points
                    ),
                    gaps=tuple(
                        ReviewGapResponse(**asdict(gap)) for gap in segment.gaps
                    ),
                )
                for segment in series.segments
            ),
        )
