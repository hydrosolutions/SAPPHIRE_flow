from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


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
    qc_status: str
    qc_flags: list[dict[str, object]]


class ForecastSummary(BaseModel):
    id: str
    station_id: str
    model_id: str
    issued_at: datetime
    parameter: str
    representation: str
    status: str
    qc_status: str
    nwp_cycle_source: str
    created_at: datetime


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
