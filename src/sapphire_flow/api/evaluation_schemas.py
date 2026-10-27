# ruff: noqa: TC001 -- FastAPI/Pydantic resolve annotations at runtime.
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from sapphire_flow.api.schemas import QcFlagResponse
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import DailyFailureReason
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
)
from sapphire_flow.types.daily_forecast_recovery import DailyRecoveryStatus
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
    QcStatus,
)


class EvaluationInputFlag(BaseModel):
    category: InputQualityCategory
    level: InputQualityLevel
    detail: str | None


class EvaluationProvenance(BaseModel):
    nwp_source: NwpCycleSource | None = None
    nwp_reference_time: datetime | None = None
    nwp_age_hours: float | None = None
    observation_staleness_hours: float | None = None
    input_quality: InputQualityLevel | None = None
    input_quality_flags: list[EvaluationInputFlag] = []


class EvaluationValue(BaseModel):
    valid_time: datetime
    value: float


class EvaluationSelected(BaseModel):
    output_id: str
    parameter: str
    source: Literal["forecast", "rejected"]
    model_id: str
    issued_at: datetime
    age_seconds: float
    representation: EnsembleRepresentation
    units: str
    time_step_seconds: float
    qc_status: QcStatus
    qc_flags: list[QcFlagResponse]
    input_quality: InputQualityLevel | None
    input_quality_flags: list[EvaluationInputFlag]
    provenance: EvaluationProvenance | None
    values: dict[str, list[EvaluationValue]] | None
    withheld: bool


class EvaluationParameterResponse(BaseModel):
    parameter: str
    selection: Literal["current", "fallback", "absent"]
    absence: Literal["no_usable_output", "no_valid_time_coverage"] | None
    selected: EvaluationSelected | None
    daily_absence: Literal["no_usable_output", "no_valid_time_coverage"] | None


class EvaluationReferenceResponse(BaseModel):
    usability: Literal["usable", "superseded", "unavailable", "unverified"] | None = (
        None
    )
    output_id: str
    source: Literal["forecast", "rejected"]
    model_id: str
    parameter: str
    issued_at: datetime


class EvaluationDailyResponse(BaseModel):
    group_id: str
    issue_time: datetime
    latest_attempt_id: str | None
    latest_state: DailyAttemptState | Literal["not_attempted", "unavailable"]
    failure_reason: DailyFailureReason | None
    authoritative_attempt_id: str | None
    member_state: DailyMemberState | None
    member_reason: DailyMemberReason | None
    required_parameters: list[str]
    references: list[EvaluationReferenceResponse]
    availability: Literal["available", "post_as_of"] = "available"
    recovery_status: DailyRecoveryStatus | None
    provenance: EvaluationProvenance | None
    withheld: bool


class EvaluationAssignmentResponse(BaseModel):
    model_id: str
    priority: int
    declared_parameters: list[str]


class EvaluationModelDiagnosticResponse(BaseModel):
    availability: Literal["available", "post_as_of"] = "available"
    model_id: str
    freshness: Literal["current", "stale", "absent", "unknown"]
    latest_result_at: datetime | None
    age_seconds: float | None
    diagnostic_window_start: datetime | None
    records: list[EvaluationSelected]
    evidence_authority: Literal[
        "finalized_summary", "stored_only_unverified", "unknown"
    ]
    stored_result: EvaluationReferenceResponse | None
    group_id: str | None
    issue_time: datetime | None
    latest_attempt_id: str | None
    latest_state: DailyAttemptState | None
    authoritative_attempt_id: str | None


class EvaluationForecastsResponse(BaseModel):
    station_id: str
    as_of: datetime
    valid_at: datetime
    issue_time: datetime
    diagnostic_window_start: datetime
    data_use: Literal["standard"] = "standard"
    parameters: list[EvaluationParameterResponse]
    daily: list[EvaluationDailyResponse]
    assignments: list[EvaluationAssignmentResponse]
    models: list[EvaluationModelDiagnosticResponse]
