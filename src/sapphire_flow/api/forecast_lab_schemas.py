"""Plan 198 T1 — Pydantic v2 boundary models for the Forecast Lab snapshot
document (``forecast-lab-snapshot/v1``).

These are the ONLY place the wire shape is defined — the generated JSON
Schema (``docs/spec/forecast-lab-snapshot-v1.schema.json``) is derived from
these models (D15), and both the REST route (T5) and the CLI export (T6)
serialise exactly this model, produced by the single assembly function
``services.forecast_lab.snapshot.build_snapshot()`` (D1).

Per CLAUDE.md, Pydantic is used here because this module IS the system
boundary (the wire contract to the separate SAPPHIRE-flow-map project) —
these are never used as internal domain types elsewhere in this repo.

See ``docs/plans/198-forecast-lab-snapshot-export.md`` § The v1 document
shape (authoritative) for the annotated example this file implements, and
its deltas-from-the-request table for why each field looks the way it does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, WithJsonSchema
from pydantic.functional_serializers import PlainSerializer

# ---------------------------------------------------------------------------
# Shared timestamp type — every timestamp in the document is RFC 3339 UTC
# with a literal "Z" suffix (AC4), never a "+00:00" offset.
# ---------------------------------------------------------------------------


def _rfc3339_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


Rfc3339Utc = Annotated[
    datetime,
    PlainSerializer(_rfc3339_z, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


class ForecastLabModel(BaseModel):
    """Base for every model in this module — forbids unknown fields so a
    typo in builder code fails loudly rather than silently dropping data."""

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Shared leaves
# ---------------------------------------------------------------------------


class GeoCoordSchema(ForecastLabModel):
    longitude: float
    latitude: float
    crs: Literal["EPSG:4326"] = "EPSG:4326"


class StationSchema(ForecastLabModel):
    code: str
    network: Literal["bafu"] = "bafu"
    name: str
    display_name: str | None
    river: str | None
    location: GeoCoordSchema
    basin_area_km2: float | None
    active: bool


SourceStatusValue = Literal["ok", "error", "missing"]


class SourceStatusSchema(ForecastLabModel):
    status: SourceStatusValue
    latest_available_at: Rfc3339Utc | None
    message: str | None


OverallStatusValue = Literal["ok", "partial", "unavailable"]


class StatusBlockSchema(ForecastLabModel):
    overall: OverallStatusValue
    observations: SourceStatusSchema
    bafu_forecasts: SourceStatusSchema
    sapphire_forecasts: SourceStatusSchema


class ComparisonSemanticsSchema(ForecastLabModel):
    variable: Literal["discharge"] = "discharge"
    unit: Literal["m3/s"] = "m3/s"
    display_run_rule: str
    daily_aggregation: str
    bafu_daily_completeness_minimum: int
    observation_daily_completeness_minimum: int
    sapphire_quantile_method: Literal["linear"] = "linear"


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


class ObservationPointSchema(ForecastLabModel):
    valid_time: Rfc3339Utc
    value: float
    qc_status: Literal["qc_passed"] = "qc_passed"


class ObservationsSectionSchema(ForecastLabModel):
    variable: Literal["discharge"] = "discharge"
    unit: Literal["m3/s"] = "m3/s"
    native_step_seconds: int
    window_start: Rfc3339Utc
    window_end: Rfc3339Utc
    latest_available_at: Rfc3339Utc | None
    points: list[ObservationPointSchema]


# ---------------------------------------------------------------------------
# BAFU forecast (available / unavailable — D6/D13)
# ---------------------------------------------------------------------------


class QuantileEnvelopeSchema(ForecastLabModel):
    valid_time: Rfc3339Utc
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None


class BafuForecastAvailableSchema(ForecastLabModel):
    available: Literal[True] = True
    source: Literal["bafu"] = "bafu"
    source_product: Literal["hydrodaten_plot"] = "hydrodaten_plot"
    station_code: str
    variable: Literal["discharge"] = "discharge"
    unit: Literal["m3/s"] = "m3/s"
    run_id: str
    issued_at: Rfc3339Utc
    inventory_produced_at: Rfc3339Utc
    licence_status: Literal["unresolved"] = "unresolved"
    native_step_seconds: int
    horizon_start: Rfc3339Utc
    horizon_end: Rfc3339Utc
    point_count: int
    quality_flags: list[str]
    points: list[QuantileEnvelopeSchema]


class BafuForecastUnavailableSchema(ForecastLabModel):
    available: Literal[False] = False
    # Not a closed Literal set (unlike SapphireUnavailableReason below, D19):
    # D13's causes are "archive file missing or unparseable" and D6's
    # unreconstructable-geometry "parse_error" — a short machine-readable
    # slug, e.g. "archive_not_mounted" (D2-alt), "no_matching_run",
    # "parse_error".
    reason: str
    message: str | None = None


BafuForecastEntry = Annotated[
    BafuForecastAvailableSchema | BafuForecastUnavailableSchema,
    Field(discriminator="available"),
]


# ---------------------------------------------------------------------------
# SAPPHIRE forecasts (one entry per assigned model — D5/D12/D19)
# ---------------------------------------------------------------------------


class SapphireModelRefSchema(ForecastLabModel):
    key: str


class SapphireModelSchema(ForecastLabModel):
    key: str
    display_name: str
    artifact_id: str | None
    artifact_sha256: str | None
    code_or_image_version: str | None
    is_primary: bool


SapphireUnavailableReason = Literal["no_forecast", "unsupported_representation"]


class SapphireForecastAvailableSchema(ForecastLabModel):
    available: Literal[True] = True
    source: Literal["sapphire"] = "sapphire"
    forecast_id: str
    model: SapphireModelSchema
    variable: Literal["discharge"] = "discharge"
    unit: Literal["m3/s"] = "m3/s"
    issued_at: Rfc3339Utc
    observation_staleness_hours: float | None
    native_step_seconds: int
    ensemble_size: int
    horizon_start: Rfc3339Utc
    horizon_end: Rfc3339Utc
    points: list[QuantileEnvelopeSchema]


class SapphireForecastUnavailableSchema(ForecastLabModel):
    available: Literal[False] = False
    model: SapphireModelRefSchema
    reason: SapphireUnavailableReason


SapphireForecastEntry = Annotated[
    SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema,
    Field(discriminator="available"),
]


# ---------------------------------------------------------------------------
# Aligned daily comparison (D4)
# ---------------------------------------------------------------------------


class AlignedDailyObservationSchema(ForecastLabModel):
    value: float | None
    sample_count: int
    complete: bool


class AlignedDailyBafuSchema(ForecastLabModel):
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None
    hour_count: int
    complete: bool


class AlignedDailySapphireEntrySchema(ForecastLabModel):
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None
    complete: bool


class AlignedDailyRowSchema(ForecastLabModel):
    day_start: Rfc3339Utc
    day_end: Rfc3339Utc
    observation: AlignedDailyObservationSchema
    bafu: AlignedDailyBafuSchema | None
    # D4: keyed by model_key, not a row per model.
    sapphire: dict[str, AlignedDailySapphireEntrySchema]


# ---------------------------------------------------------------------------
# Verification (D3/O7.1 — always the insufficient_data sentinel)
# ---------------------------------------------------------------------------


class VerificationSchema(ForecastLabModel):
    status: Literal["insufficient_data"] = "insufficient_data"
    window_start: Rfc3339Utc | None = None
    window_end: Rfc3339Utc | None = None
    method_version: Literal["forecast-comparison/v1"] = "forecast-comparison/v1"
    limitations: list[str]


# ---------------------------------------------------------------------------
# Per-station availability + entry
# ---------------------------------------------------------------------------


class AvailabilitySchema(ForecastLabModel):
    observations: bool
    bafu_forecast: bool
    sapphire_forecast: bool


class StationEntrySchema(ForecastLabModel):
    station: StationSchema
    availability: AvailabilitySchema
    observations: ObservationsSectionSchema | None
    bafu_forecast: BafuForecastEntry
    sapphire_forecasts: list[SapphireForecastEntry]
    aligned_daily_comparison: list[AlignedDailyRowSchema]
    verification: VerificationSchema


# ---------------------------------------------------------------------------
# Top-level document
# ---------------------------------------------------------------------------


class ForecastLabSnapshot(ForecastLabModel):
    schema_version: Literal["forecast-lab-snapshot/v1"] = "forecast-lab-snapshot/v1"
    snapshot_id: str
    generated_at: Rfc3339Utc
    data_cutoff_at: Rfc3339Utc
    status: StatusBlockSchema
    comparison_semantics: ComparisonSemanticsSchema
    stations: list[StationEntrySchema]
