from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

from sapphire_flow.api.forecast_probabilities import (
    ForecastProbabilitiesResponse,  # noqa: TC001
)
from sapphire_flow.api.weather_evidence import WeatherUseResponse  # noqa: TC001
from sapphire_flow.store.forecast_period_codec import (
    ForecastPeriodPayload,  # noqa: TC001
)
from sapphire_flow.store.threshold_skill_codec import (
    ThresholdContextPayload,  # noqa: TC001
)

# NOT moved under TYPE_CHECKING (would trip ruff TC001): these are `Literal`
# type aliases, not classes — pydantic resolves them at class-body evaluation
# time to build each model's schema, the same reason forecast_lab.py keeps
# ForecastLabSnapshot and ModelCombinationStrategy out of TYPE_CHECKING.
from sapphire_flow.types.domain import ForecastQcRuleId, QcRuleId  # noqa: TC001
from sapphire_flow.types.enums import ObservationSource  # noqa: TC001

T = TypeVar("T")

# --- Plan 402 T3: typed flags, additive QC fields ------------------------

# A `QcFlag` never carries `raw`/`missing` (`types/domain.py:94-101` rejects
# them — RAW means QC has not run, MISSING is set directly on observations).
_QcFlagStatus = Literal["qc_passed", "qc_suspect", "qc_failed", "qc_unchecked"]
# The row-level `qc_status` CAN hold `raw`/`missing`, which a flag cannot.
_QcStatusLiteral = Literal[
    "raw", "qc_passed", "qc_suspect", "qc_failed", "missing", "qc_unchecked"
]


class QcFlagResponse(BaseModel):
    rule_id: str
    rule_version: str
    status: _QcFlagStatus
    detail: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(
        description=(
            "Number of matching records across all pages, within the caller's scope."
        )
    )
    limit: int = Field(description="Maximum records requested for this page.")
    offset: int = Field(
        description="Number of matching records skipped before this page."
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


class GeoCoordResponse(BaseModel):
    lon: float
    lat: float
    altitude_masl: float | None = None


class ThresholdResponse(BaseModel):
    danger_level: str
    parameter: str
    value: float
    source: str


class ModelAssignmentResponse(BaseModel):
    model_id: str
    model_tier: str
    time_step_hours: float
    status: str
    priority: int


class WeatherSourceResponse(BaseModel):
    nwp_source: str
    extraction_type: str
    status: str


class StationSummary(BaseModel):
    id: str = Field(
        description=(
            "Station UUID used in API paths; distinct from the provider station code."
        )
    )
    code: str = Field(
        description=(
            "Provider station code for display and lookup, not an API path identifier."
        )
    )
    name: str
    location: GeoCoordResponse
    station_kind: str
    station_status: str = Field(
        description=(
            "Configured lifecycle status, not evidence of "
            "recent observations or forecasts."
        )
    )
    network: str
    ownership: str
    measured_parameters: list[str] = Field(
        description=(
            "Parameters configured for this station; does not "
            "prove recent data availability."
        )
    )
    has_provisional_forecast: bool | None = Field(
        description=(
            "Reviewer/admin: whether a non-superseded "
            "provisional forecast exists. Consumer: null. Does "
            "not indicate freshness or successful detail "
            "access."
        )
    )


class StationDetail(StationSummary):
    basin_id: str | None = None
    timezone: str
    regulation_type: str | None = None
    forecast_targets: list[str] | None = None
    gauging_status: str
    wigos_id: str | None = None
    created_at: datetime
    updated_at: datetime
    no_floor: bool
    thresholds: list[ThresholdResponse]
    model_assignments: list[ModelAssignmentResponse]
    weather_sources: list[WeatherSourceResponse]


class ObservationResponse(BaseModel):
    id: str
    station_id: str
    timestamp: datetime = Field(
        description="Observation time, including UTC offset; not ingestion time."
    )
    parameter: str
    value: float | None = Field(
        default=None,
        description=(
            "Stored observation value; null is missing, never "
            "zero. Discharge uses m³/s and water level uses m; "
            "consult station datum metadata for its reference."
        ),
    )
    source: str
    qc_status: _QcStatusLiteral = Field(
        description=(
            "Quality-control state of this record. raw and qc_unchecked are not a pass."
        )
    )
    qc_flags: list[QcFlagResponse]
    # Plan 402 T3: the stored value (`"1.2"`, `"1.2-datum"`, …) — a code
    # generation marker, not a rule-set version (see Repository facts);
    # `null` when none is stored.
    qc_rule_version: str | None = Field(
        default=None,
        description=(
            "Stored QC code-generation marker, not a rule-set "
            "version; null when not recorded."
        ),
    )


class LatestStationObservationResponse(BaseModel):
    station_id: str
    code: str
    network: str
    measured_parameters: list[str]
    observation: ObservationResponse | None = Field(
        description=(
            "Latest visible record in the requested window, or "
            "null if none. A record may itself have a null "
            "value."
        )
    )


class LatestObservationsResponse(PaginatedResponse[LatestStationObservationResponse]):
    parameter: Literal["discharge", "water_level"]
    source: ObservationSource
    window_start: datetime = Field(
        description="Inclusive start of the latest-observation search window."
    )
    generated_at: datetime = Field(
        description=(
            "Response generation time; derive observation age "
            "from each sample timestamp."
        )
    )


class _ForecastSummaryFields(BaseModel):
    period_definition: ForecastPeriodPayload | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    id: str
    station_id: str
    model_id: str
    model_display_name: str = Field(
        default_factory=lambda data: str(data["model_id"]),
        description="Registered full display name; falls back to model_id.",
    )
    model_display_name_short: str | None = Field(
        default=None, description="Optional compact label; not a unique identifier."
    )
    model_tier: str
    issued_at: datetime = Field(
        description=(
            "Forecast issue time, distinct from storage time "
            "and the weather source run."
        )
    )
    parameter: str
    representation: str
    status: str
    qc_status: _QcStatusLiteral = Field(
        description=(
            "Quality-control state of the forecast output, separate from input quality."
        )
    )
    nwp_cycle_source: str
    created_at: datetime = Field(
        description=(
            "Time this forecast record was created; not the forecast issue time."
        )
    )
    # Plan 253 T1c / OD-2: visible to every authenticated role, no
    # role-filtering — supersedes 023:128-143 (docs/standards/security.md).
    # null = unknown (no assessment recorded, e.g. a legacy row); this is
    # distinct from an assessed forecast with zero flags ([]).
    input_quality: str | None = Field(
        default=None,
        description=(
            "Assessment of model inputs; null means no recorded "
            "assessment. Separate from output QC."
        ),
    )
    input_quality_flags: list[dict[str, object]] | None = Field(
        default=None,
        description=(
            "Recorded input assessments. category distinguishes "
            "observation from forcing; detail explains missing "
            "observations, estimated weather inputs or other "
            "degradation. Null means unknown, unlike an "
            "assessed empty list."
        ),
    )
    # Plan 402 T3: [] when none — visible to every authenticated role (D13).
    qc_flags: list[QcFlagResponse] = []


class ForecastSummary(_ForecastSummaryFields):
    # Null on deployments where human publication has not been activated.
    publication_state: (
        Literal["unpublished", "selected", "replaced", "withdrawn"] | None
    ) = None
    publication_decision_id: str | None = None
    selection_version: int | None = None
    published_at: datetime | None = None
    preservation_at_publish: Literal["verified", "backup_pending"] | None = None
    forecast_status: str | None = None
    forecast_version: int | None = None


class EnsembleResponse(BaseModel):
    period_definition: ForecastPeriodPayload | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    representation: str
    parameter: str
    units: str = Field(description="Unit for every series value, e.g. discharge m³/s.")
    forecast_horizon_steps: int
    time_step_seconds: int = Field(description="Forecast time step in seconds.")
    member_count: int
    valid_times: list[datetime] = Field(
        description="Forecast validity times in the same order as every series array."
    )
    series: dict[str, list[float]] = Field(
        description=(
            "Values keyed by member identifier or quantile "
            "level according to representation; each array "
            "aligns with valid_times."
        )
    )


class _ForecastDetailFields(BaseModel):
    threshold_probabilities: ForecastProbabilitiesResponse | None = Field(
        default=None,
        description=(
            "Per-time exceedance probabilities for this exact forecast; "
            "no window-event probability or alert decision."
        ),
    )
    weather_use: WeatherUseResponse | None = Field(
        default=None,
        description=(
            "Persisted weather-source and freshness facts used "
            "for this forecast; null means unknown, not "
            "current."
        ),
    )
    model_artifact_id: str | None = None
    nwp_cycle_reference_time: datetime | None = Field(
        default=None,
        description="Reference time of the NWP run; may differ from issued_at.",
    )
    version: int
    warm_up_source: str | None = None
    observation_staleness_hours: float | None = None
    combination_strategy: str | None = None
    source_model_ids: list[str] | None = None
    updated_at: datetime
    ensemble: EnsembleResponse


class ForecastDetail(_ForecastDetailFields, ForecastSummary):
    pass


class ProvisionalMetadata(BaseModel):
    data_use: Literal["provisional_rating"]
    badge: Literal["PROVISIONAL"]
    warnings: list[str] = Field(
        description=(
            "Persisted rating-derived input warnings. These are "
            "separate from input_quality_flags."
        )
    )
    rating_curves: list[object] = Field(max_length=0)
    rating_provenance_status: Literal["not_exposed"] = Field(
        description=(
            "Exact curves are not exposed by forecast "
            "summary/detail. Use rating-input-history for "
            "frozen daily input context."
        )
    )
    publication_applicability: Literal["not_applicable"] = Field(
        description=(
            "Provisional forecasts are outside the ordinary publication workflow."
        )
    )
    publication_state: None
    publication_decision_id: None
    selection_version: None
    published_at: None
    preservation_at_publish: None


class ProvisionalForecastSummary(ProvisionalMetadata, _ForecastSummaryFields):
    forecast_status: str | None = None
    forecast_version: int | None = None


class ProvisionalForecastDetail(_ForecastDetailFields, ProvisionalForecastSummary):
    pass


class PublicationDecisionResponse(BaseModel):
    id: str
    station_id: str
    parameter: str
    issued_at: datetime
    forecast_id: str
    actor_user_id: str
    action: Literal["publish", "withdraw"]
    selection_version: int
    forecast_version: int
    preservation_at_publish: Literal["verified", "backup_pending"] | None
    replaced_forecast_id: str | None
    replaced_decision_id: str | None
    reason_code: Literal["incorrect_forecast", "data_error", "other"] | None
    reason_text: str | None
    created_at: datetime


class ReviewForecast(ForecastDetail):
    capture_status: str
    effective_preservation_status: str
    attestation_id: str | None
    remaining_reasons: list[str]
    decisions: list[PublicationDecisionResponse]


class PublishForecastRequest(BaseModel):
    expected_forecast_version: int = Field(ge=1)
    expected_selection_version: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class WithdrawForecastRequest(BaseModel):
    expected_selection_version: int = Field(ge=1)
    reason_code: Literal["incorrect_forecast", "data_error", "other"]
    reason_text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("reason_text")
    @classmethod
    def _nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("withdrawal reason text is required")
        return value.strip()


class PublicationHistoryItem(BaseModel):
    sequence: int
    event_type: Literal["published", "replaced", "withdrawn"]
    decision: PublicationDecisionResponse
    forecast: ForecastDetail | None
    publication_state: Literal["selected", "replaced", "withdrawn"]


class PublicationHistoryPage(BaseModel):
    items: list[PublicationHistoryItem]
    next_cursor: str | None


class PublicationChangeEvent(BaseModel):
    sequence: int
    event_type: Literal["published", "replaced", "withdrawn"]
    decision_id: str
    station_id: str
    forecast_id: str
    replaced_forecast_id: str | None
    created_at: datetime
    withdrawn: bool


class PublicationChangePage(BaseModel):
    items: list[PublicationChangeEvent]
    next_cursor: str | None


class PublicationTombstone(BaseModel):
    forecast_id: str
    station_id: str
    parameter: str
    issued_at: datetime
    publication_state: Literal["withdrawn"] = "withdrawn"
    decision_id: str
    withdrawn_at: datetime


class AlertResponse(BaseModel):
    id: str
    station_id: str | None = None
    source: str
    alert_level: str
    status: str
    trigger_probability: float | None = None
    trigger_value: float | None = None
    triggered_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    first_detected_at: datetime | None = None
    model_ids: list[str]
    alert_model_strategy: str | None = None


# Plan 147 Slice C (G4): POST /alerts/{id}/acknowledge is removed from the
# v1.0 HTTP surface (returns 501) — these schemas are unused until the v1.x
# session-token/Flow-3 dashboard reinstates the endpoint. Kept (not deleted)
# so the v1.x re-activation does not need to reconstruct the shape.
class AcknowledgeRequest(BaseModel):
    acknowledged_by: str


class AcknowledgeResponse(BaseModel):
    id: str
    status: str
    acknowledged_at: datetime


class HealthResponse(BaseModel):
    status: str
    prefect_status: str
    checked_at: datetime


class PipelineHealthRecordResponse(BaseModel):
    check_type: str
    checked_at: datetime
    status: str
    subject: str
    detail: dict[str, object]
    cycle_time: datetime | None = None
    created_at: datetime


class HealthDetailResponse(BaseModel):
    items: list[PipelineHealthRecordResponse]
    total: int
    limit: int


# --- Plan 402 T1: GET /api/v1/qc/rules -----------------------------------

_QcRuleSource = Literal["config", "builtin_default"]
_QcRuleSeverity = Literal["qc_failed", "qc_suspect"]


class ObservationQcRuleResponse(BaseModel):
    rule_id: QcRuleId
    rule_version: str
    parameter: str
    time_step_seconds: int
    network: str | None
    severity: _QcRuleSeverity
    thresholds: dict[str, float | None]


class ForecastQcRuleResponse(BaseModel):
    rule_id: ForecastQcRuleId
    rule_version: str
    parameter: str
    time_step_seconds: int
    severity: _QcRuleSeverity
    thresholds: dict[str, float | None]


class ObservationQcRuleSetResponse(BaseModel):
    version: str
    source: _QcRuleSource
    selection: Literal["parameter_cadence_network"] = "parameter_cadence_network"
    rules: list[ObservationQcRuleResponse]


class ForecastQcRuleSetResponse(BaseModel):
    version: str
    source: _QcRuleSource
    selection: Literal["parameter_and_cadence"] = "parameter_and_cadence"
    rules: list[ForecastQcRuleResponse]


class QcRulesResponse(BaseModel):
    # D15/oneOf: no default — a discriminator field with a pydantic default is
    # OMITTED from the committed OpenAPI `required` list, so an external
    # client's schema validator cannot use it to pick a branch. This model is
    # only ever built by our own code (api/routes/api_review.py), which
    # always passes `scope` explicitly.
    scope: Literal["deployment"]
    observation: ObservationQcRuleSetResponse
    forecast: ForecastQcRuleSetResponse


class StationObservationQcRuleResponse(ObservationQcRuleResponse):
    # names whose value came from this station's declared override; [] = none.
    station_thresholds: list[str]
    # "no_datum": a water-level rule needing a datum at a station without one
    # — ingest skips it there (services/qc_datum.py::obs_skipped_rules). No
    # default: required (nullable) per D15, not optional (see `scope` above).
    skipped: Literal["no_datum"] | None


class StationObservationQcRuleSetResponse(BaseModel):
    version: str
    source: _QcRuleSource
    selection: Literal["parameter_cadence_network"] = "parameter_cadence_network"
    rules: list[StationObservationQcRuleResponse]


class StationQcRulesResponse(BaseModel):
    # No default — see QcRulesResponse.scope above.
    scope: Literal["station"]
    station_id: str
    # "judged": ELIGIBLE for scheduled-ingest QC (an eligible station can
    # still leave a reading qc_unchecked when no cadence/rule matches).
    station_qc: Literal["judged", "not_judged"]
    water_level_datum_masl: float | None
    observation: StationObservationQcRuleSetResponse
    forecast: ForecastQcRuleSetResponse


QcRulesUnion = Annotated[
    QcRulesResponse | StationQcRulesResponse, Field(discriminator="scope")
]


# --- Plan 402 T2: GET /api/v1/stations/{id}/skill ------------------------

_SkillSourceLiteral = Literal[
    "hindcast_nwp_archive", "hindcast_reanalysis", "operational", "transfer_validation"
]
_ForcingTypeLiteral = Literal["nwp_archive", "reanalysis"]
_FlowRegimeLiteral = Literal["low", "high", "flood"]
_EvaluatedOnLiteral = Literal[
    "training_period", "outside_training_period", "overlaps_training_period", "unknown"
]


class SkillRowResponse(BaseModel):
    model_id: str
    model_artifact_id: str
    generation_id: str | None
    skill_source: _SkillSourceLiteral
    forcing_type: _ForcingTypeLiteral | None
    computation_version: int
    time_step_seconds: int
    phase_offset_seconds: int | None
    eval_period_start: datetime
    eval_period_end: datetime
    training_period_start: datetime | None
    training_period_end: datetime | None
    evaluated_on: _EvaluatedOnLiteral
    lead_time_hours: int
    season: str | None
    flow_regime: _FlowRegimeLiteral | None
    metric: str
    score: float | None
    sample_size: int
    threshold_context: ThresholdContextPayload | None = None


class StationSkillResponse(BaseModel):
    station_id: str
    selection: Literal[
        "latest_generation_on_forecast_artifact", "explicit_artifact"
    ] = "latest_generation_on_forecast_artifact"
    rows: list[SkillRowResponse]


# --- Plan 404 T3: GET /api/v1/stations/{id}/rejected-forecasts -----------

_RejectedQcStatusLiteral = Literal[
    "qc_failed", "qc_suspect", "qc_passed", "qc_unchecked"
]
_RepresentationLiteral = Literal["members", "quantiles"]


class NonFiniteValue(BaseModel):
    """The evidence non-finite encoding (`services/forecast_evidence.py`),
    reused at the wire boundary: `repr(value)` is one of `nan`, `inf`,
    `-inf`."""

    nonfinite: Literal["nan", "inf", "-inf"]


class RejectedForecastValuePoint(BaseModel):
    valid_time: datetime
    value: float | NonFiniteValue


class RejectedForecastResponse(BaseModel):
    id: str
    attempt_id: str
    recorded_at: datetime
    station_id: str
    model_id: str
    model_artifact_id: str | None
    group_id: str | None
    issued_at: datetime
    parameter: str
    representation: _RepresentationLiteral
    units: str
    time_step_seconds: int
    qc_status: _RejectedQcStatusLiteral
    qc_flags: list[QcFlagResponse]
    # None only when `withheld` is true (D4) — keyed by member id or
    # quantile level (as a string), each series its own timeline.
    values: dict[str, list[RejectedForecastValuePoint]] | None
    withheld: bool
