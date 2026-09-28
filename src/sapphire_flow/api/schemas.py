from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

# NOT moved under TYPE_CHECKING (would trip ruff TC001): these are `Literal`
# type aliases, not classes — pydantic resolves them at class-body evaluation
# time to build each model's schema, the same reason forecast_lab.py keeps
# ForecastLabSnapshot and ModelCombinationStrategy out of TYPE_CHECKING.
from sapphire_flow.types.domain import ForecastQcRuleId, QcRuleId  # noqa: TC001

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
    total: int
    limit: int
    offset: int


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
    id: str
    code: str
    name: str
    location: GeoCoordResponse
    station_kind: str
    station_status: str
    network: str
    ownership: str
    measured_parameters: list[str]


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
    timestamp: datetime
    parameter: str
    value: float | None = None
    source: str
    qc_status: _QcStatusLiteral
    qc_flags: list[QcFlagResponse]
    # Plan 402 T3: the stored value (`"1.2"`, `"1.2-datum"`, …) — a code
    # generation marker, not a rule-set version (see Repository facts);
    # `null` when none is stored.
    qc_rule_version: str | None = None


class ForecastSummary(BaseModel):
    id: str
    station_id: str
    model_id: str
    model_tier: str
    issued_at: datetime
    parameter: str
    representation: str
    status: str
    qc_status: _QcStatusLiteral
    nwp_cycle_source: str
    created_at: datetime
    # Plan 253 T1c / OD-2: visible to every authenticated role, no
    # role-filtering — supersedes 023:128-143 (docs/standards/security.md).
    # null = unknown (no assessment recorded, e.g. a legacy row); this is
    # distinct from an assessed forecast with zero flags ([]).
    input_quality: str | None = None
    input_quality_flags: list[dict[str, object]] | None = None
    # Plan 402 T3: [] when none — visible to every authenticated role (D13).
    qc_flags: list[QcFlagResponse] = []


class EnsembleResponse(BaseModel):
    representation: str
    parameter: str
    units: str  # Canonical API unit form, e.g. discharge "m³/s".
    forecast_horizon_steps: int
    time_step_seconds: int
    member_count: int
    valid_times: list[datetime]
    series: dict[str, list[float]]


class ForecastDetail(ForecastSummary):
    model_artifact_id: str | None = None
    nwp_cycle_reference_time: datetime | None = None
    version: int
    warm_up_source: str | None = None
    observation_staleness_hours: float | None = None
    combination_strategy: str | None = None
    source_model_ids: list[str] | None = None
    updated_at: datetime
    ensemble: EnsembleResponse


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
    "training_period", "outside_training_period", "overlaps_training_period"
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
    training_period_start: datetime
    training_period_end: datetime
    evaluated_on: _EvaluatedOnLiteral
    lead_time_hours: int
    season: str | None
    flow_regime: _FlowRegimeLiteral | None
    metric: str
    score: float | None
    sample_size: int


class StationSkillResponse(BaseModel):
    station_id: str
    selection: Literal["latest_generation_on_forecast_artifact"] = (
        "latest_generation_on_forecast_artifact"
    )
    rows: list[SkillRowResponse]
