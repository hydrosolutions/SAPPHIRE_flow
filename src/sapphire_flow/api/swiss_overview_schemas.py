# ruff: noqa: TC001, TC003 -- Pydantic resolves annotations at runtime.
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from sapphire_flow.types.swiss_overview import HeadlineAbsence


class OverviewStation(BaseModel):
    id: str
    network: str
    code: str


class OverviewSample(BaseModel):
    observed_at: datetime
    value: float
    qc_status: Literal["qc_passed"] = "qc_passed"


class OverviewHour(BaseModel):
    hour_end: datetime
    sample: OverviewSample | None


class OverviewObservation(BaseModel):
    availability: Literal["available", "unavailable"]
    reason: Literal["no_eligible_observation"] | None
    latest: OverviewSample | None
    hourly: list[OverviewHour] = Field(min_length=3, max_length=3)


class OverviewReportedLevel(BaseModel):
    availability: Literal["unavailable"] = "unavailable"
    reason: Literal["not_onboarded"] = "not_onboarded"
    state: Literal["unavailable"] = "unavailable"
    level: None = None
    observed_at: None = None
    collected_at: None = None
    source: None = None
    publisher: None = None


class OverviewFlowClass(BaseModel):
    availability: Literal["unavailable"] = "unavailable"
    reason: Literal["not_onboarded"] = "not_onboarded"
    code: None = None
    label: None = None
    source_time: None = None
    collected_at: None = None
    source: None = None
    classification_scheme: None = None


class HeadlineUnavailable(BaseModel):
    state: Literal["unavailable"] = "unavailable"
    reason: HeadlineAbsence


class CmalWithheld(BaseModel):
    state: Literal["withheld"] = "withheld"
    reason: Literal["publication_gate"] = "publication_gate"
    output_id: str
    output_source: Literal["forecast", "rejected"]
    qc_status: str
    model_id: str
    issued_at: datetime


class BafuWithheld(BaseModel):
    state: Literal["withheld"] = "withheld"
    reason: Literal["publication_gate"] = "publication_gate"
    source: Literal["bafu"] = "bafu"


class OverviewInputFlag(BaseModel):
    category: str
    level: str
    detail: str | None


class HeadlineAvailable(BaseModel):
    state: Literal["available"] = "available"
    reason: None = None
    issued_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    peak_value: float
    unit: Literal["m3/s"] = "m3/s"
    peak_valid_time: datetime
    inferred_level: None = None
    threshold_source: None = None
    threshold_profile: None = None


class CmalAvailable(HeadlineAvailable):
    source: Literal["daily_cmal"] = "daily_cmal"
    output_id: str
    output_source: Literal["forecast", "rejected"]
    qc_status: str
    model_id: str
    peak_statistic: Literal["ensemble_median"] = "ensemble_median"
    input_quality: str | None
    input_quality_flags: list[OverviewInputFlag]


class BafuAvailable(HeadlineAvailable):
    source: Literal["bafu"] = "bafu"
    peak_statistic: Literal["median"] = "median"


CmalHeadline = Annotated[
    CmalAvailable | CmalWithheld | HeadlineUnavailable, Field(discriminator="state")
]
BafuHeadline = Annotated[
    BafuAvailable | BafuWithheld | HeadlineUnavailable, Field(discriminator="state")
]


class SwissOverviewItem(BaseModel):
    station: OverviewStation
    observation: OverviewObservation
    reported_level: OverviewReportedLevel = Field(default_factory=OverviewReportedLevel)
    flow_class: OverviewFlowClass = Field(default_factory=OverviewFlowClass)
    forecast: CmalHeadline
    bafu_comparison: BafuHeadline
    thresholds: list[None] = Field(default_factory=lambda: list[None](), max_length=0)


class SwissOverviewResponse(BaseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    parameter: Literal["discharge"] = "discharge"
    observation_source: Literal["measured"] = "measured"
    observation_display_unit: Literal["m3/s"] = "m3/s"
    station_count: int
    items: list[SwissOverviewItem]
