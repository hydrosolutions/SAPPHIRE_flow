"""Plan 198 T1 (extended by Plan 204 T1/T2) — Pydantic v2 boundary models
for the Forecast Lab snapshot document (``forecast-lab-snapshot/v2``).

These are the ONLY place the wire shape is defined — the generated JSON
Schema (``docs/spec/forecast-lab-snapshot-v2.schema.json``) is derived from
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

import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, WithJsonSchema
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


def _null_if_not_finite(value: Any) -> Any:
    """AC5 — a NaN/Infinity float is missing data, not a number, and `null`
    is how this contract represents a missing numeric.

    Enforced HERE, at the boundary type, rather than at each producer:
    Postgres `double precision` and parquet float columns both admit
    non-finite values, so every DB-sourced float is a potential source. An
    earlier fix sanitised only the BAFU traces and the ensemble members and
    left the scalar fields (`basin_area_km2`, `observation_staleness_hours`,
    the daily means) exposed — the independent review caught that. One
    annotated type closes the whole class."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _reject_if_not_finite(value: Any) -> Any:
    """For a REQUIRED numeric there is no `null` to fall back to, so a
    non-finite value is a data-integrity failure and must be loud.

    This is an INVARIANT, not a live code path, but the protection differs
    per field and neither is "sanitisation" everywhere:

    * `ObservationPointSchema.value` — sanitised at the source
      (`db_sources.fetch_observation_window` drops non-finite readings), as
      are the BAFU trace values and the ensemble members.
    * `GeoCoordSchema.longitude` / `latitude` — NOT sanitised. They are
      rejected earlier, at the domain type: `GeoCoord.__post_init__`
      (`types/domain.py:29-33`) tests `not (-180.0 <= lon <= 180.0)`, and
      that form rejects `NaN` as well as infinities (the `lon < -180 or
      lon > 180` form would NOT — a chained comparison against `NaN` is
      False, so its negation raises, while the disjunctive form is False and
      passes). Verified by test. A non-finite coordinate therefore fails at
      `PgStationStore` before this schema is ever constructed.

    Do NOT read this as a D13 partial-snapshot path either way — observation
    assembly has no D13 guard, so a raise here would surface as `500`."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite value in a required numeric field")
    return value


NullableFiniteFloat = Annotated[float | None, BeforeValidator(_null_if_not_finite)]
FiniteFloat = Annotated[float, BeforeValidator(_reject_if_not_finite)]


class ForecastLabModel(BaseModel):
    """Base for every model in this module — forbids unknown fields so a
    typo in builder code fails loudly rather than silently dropping data."""

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Shared leaves
# ---------------------------------------------------------------------------


class GeoCoordSchema(ForecastLabModel):
    longitude: FiniteFloat
    latitude: FiniteFloat
    crs: Literal["EPSG:4326"] = "EPSG:4326"


class StationSchema(ForecastLabModel):
    code: str
    network: Literal["bafu"] = "bafu"
    name: str
    display_name: str | None
    river: str | None
    location: GeoCoordSchema
    basin_area_km2: NullableFiniteFloat
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
    # Plan 204 T2 — this describes `representation: "members"` entries ONLY.
    # A `representation: "quantiles"` entry's p25/median/p75 are exact
    # stored levels, never run through this order-statistic method.
    sapphire_quantile_method: Literal["linear"] = "linear"


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


class ObservationPointSchema(ForecastLabModel):
    valid_time: Rfc3339Utc
    value: FiniteFloat
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
    minimum: NullableFiniteFloat
    p25: NullableFiniteFloat
    median: NullableFiniteFloat
    p75: NullableFiniteFloat
    maximum: NullableFiniteFloat


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

# Plan 204 T2 — the MEMBER-COUNT TRAP: a QUANTILES forecast's `member_count`
# is its LEVEL count, not an ensemble size, so a quantile entry must never be
# able to carry `ensemble_size`. Split into two variants, nested-discriminated
# on `representation`, so the wrong pairing (`representation: "quantiles"`
# with `ensemble_size`, or vice versa) is unrepresentable rather than merely
# tested for (the FORBID-EXTRA RULE rejects it at construction).

RepresentationValue = Literal["members", "quantiles"]


class SapphireForecastAvailableSchema(ForecastLabModel):
    """Shared base — every field common to both representations. Kept under
    this name (not renamed) because three `isinstance` call sites in
    `snapshot.py` and four in `test_snapshot.py` rely on it; it is never
    itself a union member and never appears in the generated `$defs`."""

    available: Literal[True] = True
    source: Literal["sapphire"] = "sapphire"
    forecast_id: str
    model: SapphireModelSchema
    variable: Literal["discharge"] = "discharge"
    unit: Literal["m3/s"] = "m3/s"
    issued_at: Rfc3339Utc
    observation_staleness_hours: NullableFiniteFloat
    native_step_seconds: int
    horizon_start: Rfc3339Utc
    horizon_end: Rfc3339Utc
    points: list[QuantileEnvelopeSchema]


class SapphireForecastMembersSchema(SapphireForecastAvailableSchema):
    representation: Literal["members"] = "members"
    ensemble_size: int


class SapphireForecastQuantilesSchema(SapphireForecastAvailableSchema):
    representation: Literal["quantiles"] = "quantiles"
    quantile_level_count: int


SapphireForecastAvailableEntry = Annotated[
    SapphireForecastMembersSchema | SapphireForecastQuantilesSchema,
    Field(discriminator="representation"),
]


class SapphireForecastUnavailableSchema(ForecastLabModel):
    available: Literal[False] = False
    model: SapphireModelRefSchema
    reason: SapphireUnavailableReason


SapphireForecastEntry = Annotated[
    SapphireForecastAvailableEntry | SapphireForecastUnavailableSchema,
    Field(discriminator="available"),
]


# ---------------------------------------------------------------------------
# Combined forecast (`_pooled` / `_bma` — Plan 204 T1, Gap 2)
# ---------------------------------------------------------------------------
#
# A sibling block on `StationEntrySchema`, always present and discriminated
# on `available` — NOT a `sapphire_forecasts[]` entry, because that array is
# contractually "one entry per assigned model" (D12/D17b) and the combined
# forecast has no assignment row at all. Mirrors the `bafu_forecast` union.
# Shares the MEMBER-COUNT-TRAP-safe nested split with the per-model entry
# above, for the same reason.


class CombinedForecastAvailableSchema(ForecastLabModel):
    available: Literal[True] = True
    source: Literal["sapphire"] = "sapphire"
    forecast_id: str
    model_key: str  # the sentinel actually fetched: "_pooled" or "_bma"
    combination_strategy: str
    source_model_ids: list[str]
    variable: Literal["discharge"] = "discharge"
    unit: Literal["m3/s"] = "m3/s"
    issued_at: Rfc3339Utc
    observation_staleness_hours: NullableFiniteFloat
    native_step_seconds: int
    horizon_start: Rfc3339Utc
    horizon_end: Rfc3339Utc
    points: list[QuantileEnvelopeSchema]


class CombinedForecastMembersSchema(CombinedForecastAvailableSchema):
    representation: Literal["members"] = "members"
    ensemble_size: int


class CombinedForecastQuantilesSchema(CombinedForecastAvailableSchema):
    representation: Literal["quantiles"] = "quantiles"
    quantile_level_count: int


CombinedForecastAvailableEntry = Annotated[
    CombinedForecastMembersSchema | CombinedForecastQuantilesSchema,
    Field(discriminator="representation"),
]

# strategy_primary     -> strategy is PRIMARY; no lookup performed
# no_combined_forecast -> POOLED/BMA discharge lookup returned None,
#                         or strategy is CONSENSUS (unsupported, no lookup)
CombinedForecastUnavailableReason = Literal["strategy_primary", "no_combined_forecast"]


class CombinedForecastUnavailableSchema(ForecastLabModel):
    available: Literal[False] = False
    reason: CombinedForecastUnavailableReason


CombinedForecastEntry = Annotated[
    CombinedForecastAvailableEntry | CombinedForecastUnavailableSchema,
    Field(discriminator="available"),
]


# ---------------------------------------------------------------------------
# Aligned daily comparison (D4)
# ---------------------------------------------------------------------------


class AlignedDailyObservationSchema(ForecastLabModel):
    value: NullableFiniteFloat
    sample_count: int
    complete: bool


class AlignedDailyBafuSchema(ForecastLabModel):
    minimum: NullableFiniteFloat
    p25: NullableFiniteFloat
    median: NullableFiniteFloat
    p75: NullableFiniteFloat
    maximum: NullableFiniteFloat
    hour_count: int
    complete: bool


class AlignedDailySapphireEntrySchema(ForecastLabModel):
    minimum: NullableFiniteFloat
    p25: NullableFiniteFloat
    median: NullableFiniteFloat
    p75: NullableFiniteFloat
    maximum: NullableFiniteFloat
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
    combined_forecast: CombinedForecastEntry
    aligned_daily_comparison: list[AlignedDailyRowSchema]
    verification: VerificationSchema


# ---------------------------------------------------------------------------
# Top-level document
# ---------------------------------------------------------------------------


class ForecastLabSnapshot(ForecastLabModel):
    schema_version: Literal["forecast-lab-snapshot/v2"] = "forecast-lab-snapshot/v2"
    snapshot_id: str
    generated_at: Rfc3339Utc
    data_cutoff_at: Rfc3339Utc
    status: StatusBlockSchema
    comparison_semantics: ComparisonSemanticsSchema
    stations: list[StationEntrySchema]
