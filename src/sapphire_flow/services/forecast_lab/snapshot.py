"""Plan 198 T3 — ``build_snapshot()``: the single Forecast Lab snapshot
assembly function (D1). Both the REST route (T5) and the CLI export (T6)
call this and serialise exactly what it returns — no second implementation.

Per-source guards (D13): a non-poisoning failure (archive file missing or
unparseable, unreconstructable BAFU trace geometry per D6) degrades that
source to unavailable/partial. A `SQLAlchemyError` from the shared
request-scoped transaction is NEVER caught here — it must escape to the
caller (T5's route) so it surfaces as `500`, not a partial snapshot.

`verification` is always the `insufficient_data` sentinel (D3/O7.1) —
this module never queries `hindcast_forecasts`, `hindcast_values` or
`skill_scores`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

import numpy as np
import polars as pl
import structlog

from sapphire_flow.api.forecast_lab_schemas import (
    AlignedDailyBafuSchema,
    AlignedDailyObservationSchema,
    AlignedDailyRowSchema,
    AlignedDailySapphireEntrySchema,
    AvailabilitySchema,
    BafuForecastAvailableSchema,
    BafuForecastUnavailableSchema,
    CombinedForecastAvailableSchema,
    CombinedForecastMembersSchema,
    CombinedForecastQuantilesSchema,
    CombinedForecastUnavailableSchema,
    ComparisonSemanticsSchema,
    ForecastLabSnapshot,
    GeoCoordSchema,
    ObservationPointSchema,
    ObservationsSectionSchema,
    QuantileEnvelopeSchema,
    SapphireForecastAvailableSchema,
    SapphireForecastMembersSchema,
    SapphireForecastQuantilesSchema,
    SapphireForecastUnavailableSchema,
    SapphireModelRefSchema,
    SapphireModelSchema,
    SourceStatusSchema,
    StationEntrySchema,
    StationSchema,
    StatusBlockSchema,
    VerificationSchema,
)
from sapphire_flow.services.forecast_lab.bafu_archive import (
    BafuArchiveRun,
    BafuRunUnavailable,
    read_latest_bafu_run,
)
from sapphire_flow.services.forecast_lab.db_sources import (
    fetch_active_model_assignments,
    fetch_artifact_info,
    fetch_basin_area_km2,
    fetch_latest_forecast_for_model,
    fetch_model_display,
    fetch_observation_window,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation, ModelCombinationStrategy
from sapphire_flow.types.ids import BMA_MODEL_ID, POOLED_MODEL_ID

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from sapphire_flow.api.forecast_lab_schemas import (
        CombinedForecastEntry,
        SapphireForecastEntry,
    )
    from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.station import ModelAssignment, StationConfig

log = structlog.get_logger(__name__)


class _RenderedSapphireSource(Protocol):
    """Plan 204 T1 — the structural shape the four derived views actually
    read (`_days_covered`, `_aligned_sapphire`, `_aligned_daily_comparison`,
    the roll-up filter). Both a per-model available entry and the combined
    forecast satisfy this — the combined variants carry `model_key`, not
    `model.key`, which is why this is a Protocol rather than a shared base
    class. `datetime`, NOT `UtcDatetime` (a distinct NewType): a Protocol's
    attributes are invariant, and `SapphireForecastAvailableSchema.issued_at`
    is statically `datetime` (`Rfc3339Utc` erases to `Annotated[datetime,
    ...]`), so `UtcDatetime` here would reject every implementer under
    pyright strict."""

    issued_at: datetime
    points: list[QuantileEnvelopeSchema]


# D14/T3 — comparison_semantics constants (F8/D5).
_BAFU_DAILY_COMPLETENESS_MIN_HOURS = 22
_OBSERVATION_DAILY_COMPLETENESS_MIN_SAMPLES = 130
_OBSERVATION_NATIVE_STEP_SECONDS = 600  # F8 — 10-minute grid
_DEFAULT_OBSERVATION_HOURS = 168

# D3/O7.1 — the verification sentinel is a static constant, never computed.
# Version-neutral on purpose (Plan 204 T3): this sentence is about the
# Plan 111 gate, not the document version, so it should not need touching at
# every schema_version bump.
_VERIFICATION_LIMITATIONS = (
    "Verification is not computed in this release (Plan 111 gate G1 — no "
    "BAFU-derived benchmark before licence clarity).",
    "Only two operational SAPPHIRE stations.",
    "Short comparison period.",
)

# Plan 204 T2 — the RECOGNISED-SET RULE: the exact stored quantile level set
# this deployment's fallback-tier models emit. A QUANTILES forecast is
# rendered only when EVERY `valid_time` carries exactly these seven levels
# (no missing level, no duplicate row) — anything else keeps the D5 guard.
# `min_operational_quantile_levels` (config/deployment.py) is a COUNT FLOOR,
# not a value-set constraint, and does NOT support this exactness — a
# legitimately onboarded 9-level model still fails this check.
_RECOGNISED_QUANTILE_LEVELS = frozenset({0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95})


def _verification_sentinel() -> VerificationSchema:
    return VerificationSchema(limitations=list(_VERIFICATION_LIMITATIONS))


def _quantile_summary(
    values: np.ndarray,  # type: ignore[type-arg]
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """D5 — linear-interpolation quantiles, matching
    `numpy.quantile(..., method="linear")`.

    Non-finite members (NaN/inf) are dropped first. Postgres `double
    precision` permits both, and one such member would otherwise poison
    every summary for that valid_time and emit a bare `NaN`/`Infinity`
    token into the JSON — invalid per RFC 8259 and a breach of AC5. A
    valid_time with no finite member summarises as all-`null`, which is
    how the contract already represents a missing numeric."""
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None, None, None, None, None
    minimum = float(np.min(values))
    p25 = float(np.quantile(values, 0.25, method="linear"))
    median = float(np.quantile(values, 0.5, method="linear"))
    p75 = float(np.quantile(values, 0.75, method="linear"))
    maximum = float(np.max(values))
    return minimum, p25, median, p75, maximum


def _to_station_schema(
    station: StationConfig, basin_area_km2: float | None
) -> StationSchema:
    return StationSchema(
        code=station.code,
        name=station.name,
        display_name=None,  # D7 — null until T9b
        river=None,  # D7 — null until T9b
        location=GeoCoordSchema(
            longitude=station.location.lon, latitude=station.location.lat
        ),
        basin_area_km2=basin_area_km2,
        active=station.station_status.value == "operational",
    )


def _build_observations_section(
    stores: ForecastLabStores,
    station: StationConfig,
    *,
    window_start: UtcDatetime,
    window_end: UtcDatetime,
) -> ObservationsSectionSchema:
    observations = fetch_observation_window(
        stores, station.id, window_start=window_start, window_end=window_end
    )
    points = [
        ObservationPointSchema(valid_time=o.timestamp, value=o.value)  # type: ignore[arg-type]
        for o in observations
        if o.value is not None
    ]
    latest_available_at = max((p.valid_time for p in points), default=None)
    return ObservationsSectionSchema(
        native_step_seconds=_OBSERVATION_NATIVE_STEP_SECONDS,
        window_start=window_start,
        window_end=window_end,
        latest_available_at=latest_available_at,
        points=points,
    )


def _build_bafu_entry(
    archive_base_path: Path | None, station: StationConfig, *, now: UtcDatetime
) -> BafuForecastAvailableSchema | BafuForecastUnavailableSchema:
    if archive_base_path is None:
        # D2-alt — the archive is not mounted at all (O1=no fallback, or a
        # deployment that has not yet been wired). Not the same failure as
        # "no run in the window" (D13's distinction matters downstream).
        return BafuForecastUnavailableSchema(
            reason="archive_not_mounted",
            message="the BAFU forecast archive is not mounted on this deployment",
        )
    result = read_latest_bafu_run(archive_base_path, station.code, now=now)
    if isinstance(result, BafuRunUnavailable):
        return BafuForecastUnavailableSchema(
            reason=result.reason, message=result.message
        )
    return _to_bafu_available_schema(result)


def _to_bafu_available_schema(run: BafuArchiveRun) -> BafuForecastAvailableSchema:
    return BafuForecastAvailableSchema(
        station_code=run.station_code,
        run_id=run.run_id,
        issued_at=run.issued_at,
        inventory_produced_at=run.inventory_produced_at,
        native_step_seconds=run.native_step_seconds,
        horizon_start=run.horizon_start,
        horizon_end=run.horizon_end,
        point_count=run.point_count,
        quality_flags=list(run.quality_flags),
        points=[
            QuantileEnvelopeSchema(
                valid_time=p.valid_time,
                minimum=p.minimum,
                p25=p.p25,
                median=p.median,
                p75=p.p75,
                maximum=p.maximum,
            )
            for p in run.points
        ],
    )


def _ensemble_points(ensemble_values: pl.DataFrame) -> list[QuantileEnvelopeSchema]:
    valid_times = sorted(ensemble_values["valid_time"].unique().to_list())
    points: list[QuantileEnvelopeSchema] = []
    for vt in valid_times:
        values = ensemble_values.filter(pl.col("valid_time") == vt)["value"].to_numpy()
        minimum, p25, median, p75, maximum = _quantile_summary(values)
        points.append(
            QuantileEnvelopeSchema(
                valid_time=ensure_utc(vt),
                minimum=minimum,
                p25=p25,
                median=median,
                p75=p75,
                maximum=maximum,
            )
        )
    return points


def _is_renderable(forecast: OperationalForecast) -> bool:
    """Plan 204 T2 — the ONE shared predicate for "can this forecast be
    rendered": MEMBERS always is; QUANTILES is renderable only when every
    `valid_time` carries EXACTLY the RECOGNISED-SET RULE's seven levels (no
    missing level, no duplicate row). Called from BOTH the primary pre-pass
    and the per-entry render decision in `_sapphire_entries` — a required
    implementation constraint, not a convenience: a second, independently
    written check here is exactly the drift the plan warns against."""
    if forecast.representation is EnsembleRepresentation.MEMBERS:
        return True
    if forecast.representation is not EnsembleRepresentation.QUANTILES:
        return False
    values = forecast.ensemble.values
    for vt in values["valid_time"].unique().to_list():
        levels: list[float] = values.filter(pl.col("valid_time") == vt)[
            "quantile"
        ].to_list()
        # Row-count-plus-uniqueness (not set-equality alone): a duplicate
        # `0.25` row alongside the full seven-level set satisfies set
        # equality while leaving two candidate values for `p25`.
        if len(levels) != len(_RECOGNISED_QUANTILE_LEVELS):
            return False
        if set(levels) != set(_RECOGNISED_QUANTILE_LEVELS):
            return False
    return True


def _quantile_level_value(frame: pl.DataFrame, level: float) -> float | None:
    matches = frame.filter(pl.col("quantile") == level)["value"]
    return None if matches.is_empty() else float(matches[0])


def _quantile_envelope_points(
    ensemble_values: pl.DataFrame,
) -> list[QuantileEnvelopeSchema]:
    """Plan 204 T2 — a QUANTILES forecast's envelope is an EXACT lookup of
    the stored `0.25`/`0.50`/`0.75` levels, never `_quantile_summary`'s
    order-statistic path. `minimum`/`maximum` are always `null` (owner
    decision, option (a)): a 7-level quantile forecast does not contain the
    ensemble extremes, and fabricating them from `0.05`/`0.95` is exactly
    the error D5's guard exists to prevent."""
    valid_times = sorted(ensemble_values["valid_time"].unique().to_list())
    points: list[QuantileEnvelopeSchema] = []
    for vt in valid_times:
        frame = ensemble_values.filter(pl.col("valid_time") == vt)
        points.append(
            QuantileEnvelopeSchema(
                valid_time=ensure_utc(vt),
                minimum=None,
                p25=_quantile_level_value(frame, 0.25),
                median=_quantile_level_value(frame, 0.50),
                p75=_quantile_level_value(frame, 0.75),
                maximum=None,
            )
        )
    return points


def _display_name(stores: ForecastLabStores, model_id: ModelId) -> str:
    record = fetch_model_display(stores, model_id)
    if record is not None:
        return record.display_name
    return str(model_id).replace("_", " ").title()


def _sapphire_entries(
    stores: ForecastLabStores, station: StationConfig
) -> list[SapphireForecastEntry]:
    """D12/D17b/D19/D5/D20, extended by Plan 204 T2 — one entry per ACTIVE
    assignment, pre-sorted `(priority asc, model_id asc)` by
    `fetch_active_model_assignments`. The first entry this builder actually
    RENDERS (`_is_renderable` — members, or a recognised quantile set) wins
    `is_primary`; a model that cannot be rendered or has no forecast can
    never win it."""
    assignments = fetch_active_model_assignments(stores, station.id)
    fetched: list[tuple[ModelAssignment, OperationalForecast | None]] = [
        (a, fetch_latest_forecast_for_model(stores, station.id, a.model_id))
        for a in assignments
    ]
    primary_model_id = next(
        (a.model_id for a, f in fetched if f is not None and _is_renderable(f)),
        None,
    )

    entries: list[SapphireForecastEntry] = []
    for assignment, forecast in fetched:
        model_id = assignment.model_id
        if forecast is None:
            entries.append(
                SapphireForecastUnavailableSchema(
                    model=SapphireModelRefSchema(key=str(model_id)),
                    reason="no_forecast",
                )
            )
            continue
        if not _is_renderable(forecast):
            entries.append(
                SapphireForecastUnavailableSchema(
                    model=SapphireModelRefSchema(key=str(model_id)),
                    reason="unsupported_representation",
                )
            )
            continue

        ensemble = forecast.ensemble
        artifact_info = fetch_artifact_info(stores, forecast.model_artifact_id)
        model_schema = SapphireModelSchema(
            key=str(model_id),
            display_name=_display_name(stores, model_id),
            artifact_id=str(forecast.model_artifact_id)
            if forecast.model_artifact_id
            else None,
            artifact_sha256=artifact_info.artifact_sha256,
            code_or_image_version=artifact_info.source_commit,
            is_primary=model_id == primary_model_id,
        )
        if forecast.representation is EnsembleRepresentation.MEMBERS:
            points = _ensemble_points(ensemble.values)
            entries.append(
                SapphireForecastMembersSchema(
                    forecast_id=str(forecast.id),
                    model=model_schema,
                    issued_at=forecast.issued_at,
                    observation_staleness_hours=forecast.observation_staleness_hours,
                    native_step_seconds=int(ensemble.time_step.total_seconds()),
                    ensemble_size=ensemble.member_count,
                    horizon_start=points[0].valid_time,
                    horizon_end=points[-1].valid_time,
                    points=points,
                )
            )
        else:  # QUANTILES — `_is_renderable` already proved the recognised set.
            points = _quantile_envelope_points(ensemble.values)
            entries.append(
                SapphireForecastQuantilesSchema(
                    forecast_id=str(forecast.id),
                    model=model_schema,
                    issued_at=forecast.issued_at,
                    observation_staleness_hours=forecast.observation_staleness_hours,
                    native_step_seconds=int(ensemble.time_step.total_seconds()),
                    quantile_level_count=ensemble.member_count,
                    horizon_start=points[0].valid_time,
                    horizon_end=points[-1].valid_time,
                    points=points,
                )
            )
    return entries


def _build_combined_forecast(
    stores: ForecastLabStores,
    station: StationConfig,
    *,
    combination_strategy: ModelCombinationStrategy,
) -> CombinedForecastEntry:
    """Plan 204 T1, decision 3 — strategy-gated, never fetched
    unconditionally: `ForecastStore.fetch_latest_forecast()` has no age or
    current-mode constraint, so an unconditional fetch would keep exporting
    a stale `_pooled` row forever after a `pooled` -> `primary` switch.
    Dispatch is over the WHOLE enum (exhaustive `match`), never a `PRIMARY`
    special case with an "everything else fetches `_pooled`" tail — that
    shape would leak a stale `_pooled` row under `BMA`/`CONSENSUS`."""
    match combination_strategy:
        case ModelCombinationStrategy.PRIMARY:
            return CombinedForecastUnavailableSchema(reason="strategy_primary")
        case ModelCombinationStrategy.POOLED:
            model_id = POOLED_MODEL_ID
        case ModelCombinationStrategy.BMA:
            model_id = BMA_MODEL_ID
        case ModelCombinationStrategy.CONSENSUS:
            # Unsupported (the cycle raises NotImplementedError for it, and
            # `_consensus` is never written) — no query is issued. This is
            # an implementation note, not an observable requirement: "no
            # fetch" and "fetch then discard" are indistinguishable in the
            # document, so it is deliberately not tested.
            return CombinedForecastUnavailableSchema(reason="no_combined_forecast")

    forecast = fetch_latest_forecast_for_model(stores, station.id, model_id)
    if forecast is None:
        return CombinedForecastUnavailableSchema(reason="no_combined_forecast")

    # The SAME `_is_renderable` predicate the per-model path uses (T2). The
    # combined block is not exempt: a QUANTILES combination whose levels are
    # not the RECOGNISED SET would otherwise reach
    # `_quantile_envelope_points()` directly and export `available: true`
    # with `null` quartiles, while daily alignment still marked it
    # `complete: true` — precisely the mislabelling D5's guard exists to
    # prevent, and precisely the drift `_is_renderable`'s docstring forbids.
    # Unreachable while Plan 026 emits members; a latent trap the moment it
    # does not.
    if not _is_renderable(forecast):
        return CombinedForecastUnavailableSchema(reason="no_combined_forecast")

    # Reproduces the persisted DB provenance exactly (AC4) — no independent
    # claim about which model contributed to which parameter. Both fields
    # are nullable in storage, but the combination writer
    # (forecast_combination.py) always sets them on any row it writes for
    # a `_pooled`/`_bma` model id, so a NULL here means the row is corrupt
    # or was written by something other than the combination writer. Either
    # way, substituting the requested strategy or an empty source list would
    # silently fabricate lineage — fail loudly instead.
    if forecast.combination_strategy is None or forecast.source_model_ids is None:
        raise ValueError(
            "combined forecast row is missing provenance: "
            f"forecast_id={forecast.id} model_id={model_id} "
            f"combination_strategy={forecast.combination_strategy!r} "
            f"source_model_ids={forecast.source_model_ids!r}"
        )
    combination_strategy_value = forecast.combination_strategy
    source_model_ids = [str(m) for m in forecast.source_model_ids]
    ensemble = forecast.ensemble

    if forecast.representation is EnsembleRepresentation.MEMBERS:
        points = _ensemble_points(ensemble.values)
        return CombinedForecastMembersSchema(
            forecast_id=str(forecast.id),
            model_key=str(model_id),
            combination_strategy=combination_strategy_value,
            source_model_ids=source_model_ids,
            issued_at=forecast.issued_at,
            observation_staleness_hours=forecast.observation_staleness_hours,
            native_step_seconds=int(ensemble.time_step.total_seconds()),
            ensemble_size=ensemble.member_count,
            horizon_start=points[0].valid_time,
            horizon_end=points[-1].valid_time,
            points=points,
        )
    # QUANTILES — not reachable today (Plan 026 combination emits members).
    # `_is_renderable` above has already proved the RECOGNISED SET, exactly
    # as it does for the per-model entry (T2), so neither the type nor the
    # validation can drift between the two paths.
    quantile_points = _quantile_envelope_points(ensemble.values)
    return CombinedForecastQuantilesSchema(
        forecast_id=str(forecast.id),
        model_key=str(model_id),
        combination_strategy=combination_strategy_value,
        source_model_ids=source_model_ids,
        issued_at=forecast.issued_at,
        observation_staleness_hours=forecast.observation_staleness_hours,
        native_step_seconds=int(ensemble.time_step.total_seconds()),
        quantile_level_count=ensemble.member_count,
        horizon_start=quantile_points[0].valid_time,
        horizon_end=quantile_points[-1].valid_time,
        points=quantile_points,
    )


def _rendered_sapphire_sources(
    sapphire_entries: list[SapphireForecastEntry],
    combined_entry: CombinedForecastEntry,
) -> list[tuple[str, _RenderedSapphireSource]]:
    """Plan 204 T1 — the ONE shared "rendered SAPPHIRE sources" list: the
    available `sapphire_entries` plus, when present, the combined block
    under its sentinel key (`model_key`). `_days_covered`,
    `_aligned_sapphire`, `_aligned_daily_comparison` and the roll-up filter
    in `build_snapshot` all consume THIS list — no view may filter
    `sapphire_entries` for availability on its own, or a fifth derived view
    added later forgets the combined block, which has already happened once
    in this plan's drafting."""
    sources: list[tuple[str, _RenderedSapphireSource]] = [
        (e.model.key, e)
        for e in sapphire_entries
        if isinstance(e, SapphireForecastAvailableSchema)
    ]
    if isinstance(combined_entry, CombinedForecastAvailableSchema):
        sources.append((combined_entry.model_key, combined_entry))
    return sources


def _day_bounds(day_start: UtcDatetime) -> UtcDatetime:
    return ensure_utc(day_start + timedelta(days=1))


def _days_covered(
    bafu_entry: BafuForecastAvailableSchema | BafuForecastUnavailableSchema,
    sapphire_sources: list[tuple[str, _RenderedSapphireSource]],
) -> list[UtcDatetime]:
    """D4, extended by Plan 204 T1 — the union of UTC days spanned by the
    available BAFU run and every rendered SAPPHIRE source's horizon
    (per-model entries plus the combined block). Empty when no source has
    an available run for this station."""
    days: set[UtcDatetime] = set()
    if isinstance(bafu_entry, BafuForecastAvailableSchema):
        for p in bafu_entry.points:
            days.add(
                ensure_utc(
                    p.valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
                )
            )
    for _key, source in sapphire_sources:
        for p in source.points:
            days.add(
                ensure_utc(
                    p.valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
                )
            )
    return sorted(days)


def _aligned_observation(
    observations_section: ObservationsSectionSchema,
    *,
    day_start: UtcDatetime,
    day_end: UtcDatetime,
) -> AlignedDailyObservationSchema:
    day_points = [
        p.value
        for p in observations_section.points
        if day_start <= p.valid_time < day_end
    ]
    sample_count = len(day_points)
    value = float(np.mean(day_points)) if day_points else None
    return AlignedDailyObservationSchema(
        value=value,
        sample_count=sample_count,
        complete=sample_count >= _OBSERVATION_DAILY_COMPLETENESS_MIN_SAMPLES,
    )


def _aligned_bafu(
    bafu_entry: BafuForecastAvailableSchema | BafuForecastUnavailableSchema,
    *,
    day_start: UtcDatetime,
    day_end: UtcDatetime,
) -> AlignedDailyBafuSchema | None:
    if not isinstance(bafu_entry, BafuForecastAvailableSchema):
        return None
    day_points = [p for p in bafu_entry.points if day_start <= p.valid_time < day_end]
    hour_count = len(day_points)
    if hour_count == 0:
        return AlignedDailyBafuSchema(
            minimum=None,
            p25=None,
            median=None,
            p75=None,
            maximum=None,
            hour_count=0,
            complete=False,
        )

    def _mean(field: str) -> float | None:
        values = [
            getattr(p, field) for p in day_points if getattr(p, field) is not None
        ]
        return float(np.mean(values)) if values else None

    return AlignedDailyBafuSchema(
        minimum=_mean("minimum"),
        p25=_mean("p25"),
        median=_mean("median"),
        p75=_mean("p75"),
        maximum=_mean("maximum"),
        hour_count=hour_count,
        complete=hour_count >= _BAFU_DAILY_COMPLETENESS_MIN_HOURS,
    )


def _aligned_sapphire(
    sapphire_sources: list[tuple[str, _RenderedSapphireSource]],
    *,
    day_start: UtcDatetime,
    day_end: UtcDatetime,
) -> dict[str, AlignedDailySapphireEntrySchema]:
    """D4, extended by Plan 204 T1 — keyed by the render key (`model.key`
    for an assigned model, the fetched sentinel for the combined block).
    `AlignedDailySapphireEntrySchema` carries no per-model metadata, so
    folding the combined block in costs nothing structurally."""
    result: dict[str, AlignedDailySapphireEntrySchema] = {}
    for key, source in sapphire_sources:
        day_points = [p for p in source.points if day_start <= p.valid_time < day_end]
        if not day_points:
            continue

        def _mean(
            field: str, pts: list[QuantileEnvelopeSchema] = day_points
        ) -> float | None:
            values = [getattr(p, field) for p in pts if getattr(p, field) is not None]
            return float(np.mean(values)) if values else None

        result[key] = AlignedDailySapphireEntrySchema(
            minimum=_mean("minimum"),
            p25=_mean("p25"),
            median=_mean("median"),
            p75=_mean("p75"),
            maximum=_mean("maximum"),
            complete=True,
        )
    return result


def _aligned_daily_comparison(
    observations_section: ObservationsSectionSchema,
    bafu_entry: BafuForecastAvailableSchema | BafuForecastUnavailableSchema,
    sapphire_sources: list[tuple[str, _RenderedSapphireSource]],
) -> list[AlignedDailyRowSchema]:
    rows: list[AlignedDailyRowSchema] = []
    for day_start in _days_covered(bafu_entry, sapphire_sources):
        day_end = _day_bounds(day_start)
        rows.append(
            AlignedDailyRowSchema(
                day_start=day_start,
                day_end=day_end,
                observation=_aligned_observation(
                    observations_section, day_start=day_start, day_end=day_end
                ),
                bafu=_aligned_bafu(bafu_entry, day_start=day_start, day_end=day_end),
                sapphire=_aligned_sapphire(
                    sapphire_sources, day_start=day_start, day_end=day_end
                ),
            )
        )
    return rows


def _aggregate_source_status(
    available_flags: list[bool], available_times: list[UtcDatetime], *, kind: str
) -> SourceStatusSchema:
    """D16a per-source status, rolled up over every station in this
    request: `ok` if every eligible station has this source available,
    `missing` if none do (including zero eligible stations), `error` if
    some but not all do. See docs/plans/198-forecast-lab-snapshot-export.md
    D16a and docs/spec/forecast-lab-snapshot.md for the consumer-facing
    rule and its N=1 degenerate case."""
    total = len(available_flags)
    ok_count = sum(available_flags)
    latest = max(available_times) if available_times else None
    if total == 0 or ok_count == 0:
        return SourceStatusSchema(
            status="missing",
            latest_available_at=None,
            message=None if total == 0 else f"no station has {kind}",
        )
    if ok_count == total:
        return SourceStatusSchema(status="ok", latest_available_at=latest, message=None)
    return SourceStatusSchema(
        status="error",
        latest_available_at=latest,
        message=f"{total - ok_count} of {total} stations missing {kind}",
    )


def _overall_status(status_block_sources: list[SourceStatusSchema]) -> str:
    """D16 rule, applied in order: all ok -> ok; none ok -> unavailable;
    else -> partial."""
    if all(s.status == "ok" for s in status_block_sources):
        return "ok"
    if all(s.status != "ok" for s in status_block_sources):
        return "unavailable"
    return "partial"


def build_snapshot(
    stores: ForecastLabStores,
    *,
    stations: list[StationConfig],
    archive_base_path: Path | None,
    observation_hours: int = _DEFAULT_OBSERVATION_HOURS,
    combination_strategy: ModelCombinationStrategy = ModelCombinationStrategy.PRIMARY,
    clock: Callable[[], UtcDatetime],
) -> ForecastLabSnapshot:
    """D1, extended by Plan 204 T1 — the single assembly function.
    `stations` is the already resolved (eligible + scoped, D8/D17) set the
    caller wants rendered; this function does no HTTP, no scoping, and no
    station eligibility filtering of its own. `clock` is injected (D20) —
    `generated_at` and the `snapshot_id` derived from it are fully
    determined by it, never by `datetime.now()`. `combination_strategy`
    defaults to `PRIMARY` — exactly the pre-Plan-204 behaviour (no combined
    block) — but both real callers (the route, the CLI) inject the
    deployment-configured value explicitly."""
    generated_at = ensure_utc(clock())
    data_cutoff_at = generated_at
    window_start = ensure_utc(generated_at - timedelta(hours=observation_hours))

    ordered_stations = sorted(stations, key=lambda s: s.code)

    station_entries: list[StationEntrySchema] = []
    obs_flags: list[bool] = []
    obs_times: list[UtcDatetime] = []
    bafu_flags: list[bool] = []
    bafu_times: list[UtcDatetime] = []
    sapphire_flags: list[bool] = []
    sapphire_times: list[UtcDatetime] = []

    for station in ordered_stations:
        basin_area_km2 = fetch_basin_area_km2(stores, station.basin_id)
        observations_section = _build_observations_section(
            stores, station, window_start=window_start, window_end=data_cutoff_at
        )
        bafu_entry = _build_bafu_entry(archive_base_path, station, now=data_cutoff_at)
        sapphire_entries = _sapphire_entries(stores, station)
        combined_entry = _build_combined_forecast(
            stores, station, combination_strategy=combination_strategy
        )
        # The ONE shared "rendered SAPPHIRE sources" list (Plan 204 T1) —
        # every derived view below consumes this, not `sapphire_entries`
        # directly, so the combined block cannot be forgotten by a future
        # view.
        rendered_sources = _rendered_sapphire_sources(sapphire_entries, combined_entry)

        obs_available = len(observations_section.points) > 0
        bafu_available = isinstance(bafu_entry, BafuForecastAvailableSchema)
        sapphire_available = len(rendered_sources) > 0

        obs_flags.append(obs_available)
        if observations_section.latest_available_at is not None:
            obs_times.append(ensure_utc(observations_section.latest_available_at))
        bafu_flags.append(bafu_available)
        if isinstance(bafu_entry, BafuForecastAvailableSchema):
            bafu_times.append(ensure_utc(bafu_entry.issued_at))
        sapphire_flags.append(sapphire_available)
        if rendered_sources:
            sapphire_times.append(
                ensure_utc(max(src.issued_at for _key, src in rendered_sources))
            )

        station_entries.append(
            StationEntrySchema(
                station=_to_station_schema(station, basin_area_km2),
                availability=AvailabilitySchema(
                    observations=obs_available,
                    bafu_forecast=bafu_available,
                    sapphire_forecast=sapphire_available,
                ),
                observations=observations_section,
                bafu_forecast=bafu_entry,
                sapphire_forecasts=sapphire_entries,
                combined_forecast=combined_entry,
                aligned_daily_comparison=_aligned_daily_comparison(
                    observations_section, bafu_entry, rendered_sources
                ),
                verification=_verification_sentinel(),
            )
        )

    status_observations = _aggregate_source_status(
        obs_flags, obs_times, kind="observations"
    )
    status_bafu = _aggregate_source_status(
        bafu_flags, bafu_times, kind="a BAFU run in the requested window"
    )
    status_sapphire = _aggregate_source_status(
        sapphire_flags, sapphire_times, kind="a SAPPHIRE forecast"
    )
    status = StatusBlockSchema(
        overall=_overall_status(  # type: ignore[arg-type]
            [status_observations, status_bafu, status_sapphire]
        ),
        observations=status_observations,
        bafu_forecasts=status_bafu,
        sapphire_forecasts=status_sapphire,
    )

    snapshot_id = f"fls2-{generated_at.strftime('%Y%m%dT%H%M%SZ')}"

    return ForecastLabSnapshot(
        snapshot_id=snapshot_id,
        generated_at=generated_at,
        data_cutoff_at=data_cutoff_at,
        status=status,
        comparison_semantics=ComparisonSemanticsSchema(
            display_run_rule="latest available run from each source",
            daily_aggregation="UTC mean over [day_start, next_day_start)",
            bafu_daily_completeness_minimum=_BAFU_DAILY_COMPLETENESS_MIN_HOURS,
            observation_daily_completeness_minimum=_OBSERVATION_DAILY_COMPLETENESS_MIN_SAMPLES,
        ),
        stations=station_entries,
    )
