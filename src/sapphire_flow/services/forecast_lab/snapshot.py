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
from typing import TYPE_CHECKING

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
    ComparisonSemanticsSchema,
    ForecastLabSnapshot,
    GeoCoordSchema,
    ObservationPointSchema,
    ObservationsSectionSchema,
    QuantileEnvelopeSchema,
    SapphireForecastAvailableSchema,
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
from sapphire_flow.types.enums import EnsembleRepresentation

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.station import ModelAssignment, StationConfig

log = structlog.get_logger(__name__)

# D14/T3 — comparison_semantics constants (F8/D5).
_BAFU_DAILY_COMPLETENESS_MIN_HOURS = 22
_OBSERVATION_DAILY_COMPLETENESS_MIN_SAMPLES = 130
_OBSERVATION_NATIVE_STEP_SECONDS = 600  # F8 — 10-minute grid
_DEFAULT_OBSERVATION_HOURS = 168

# D3/O7.1 — the verification sentinel is a static constant, never computed.
_VERIFICATION_LIMITATIONS = (
    "Verification is not computed in v1 (Plan 111 gate G1 — no "
    "BAFU-derived benchmark before licence clarity).",
    "Only two operational SAPPHIRE stations.",
    "Short comparison period.",
)


def _verification_sentinel() -> VerificationSchema:
    return VerificationSchema(limitations=list(_VERIFICATION_LIMITATIONS))


def _quantile_summary(
    values: np.ndarray,  # type: ignore[type-arg]
) -> tuple[float, float, float, float, float]:
    """D5 — linear-interpolation quantiles, matching
    `numpy.quantile(..., method="linear")`."""
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


def _display_name(stores: ForecastLabStores, model_id: ModelId) -> str:
    record = fetch_model_display(stores, model_id)
    if record is not None:
        return record.display_name
    return str(model_id).replace("_", " ").title()


def _sapphire_entries(
    stores: ForecastLabStores, station: StationConfig
) -> list[SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema]:
    """D12/D17b/D19/D5/D20 — one entry per ACTIVE assignment, pre-sorted
    `(priority asc, model_id asc)` by `fetch_active_model_assignments`. The
    first entry whose forecast is available (representation MEMBERS) wins
    `is_primary` — a model that cannot be summarised or has no forecast
    can never win it."""
    assignments = fetch_active_model_assignments(stores, station.id)
    fetched: list[tuple[ModelAssignment, OperationalForecast | None]] = [
        (a, fetch_latest_forecast_for_model(stores, station.id, a.model_id))
        for a in assignments
    ]
    primary_model_id = next(
        (
            a.model_id
            for a, f in fetched
            if f is not None and f.representation is EnsembleRepresentation.MEMBERS
        ),
        None,
    )

    entries: list[
        SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema
    ] = []
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
        if forecast.representation is not EnsembleRepresentation.MEMBERS:
            entries.append(
                SapphireForecastUnavailableSchema(
                    model=SapphireModelRefSchema(key=str(model_id)),
                    reason="unsupported_representation",
                )
            )
            continue

        ensemble = forecast.ensemble
        points = _ensemble_points(ensemble.values)
        artifact_info = fetch_artifact_info(stores, forecast.model_artifact_id)
        entries.append(
            SapphireForecastAvailableSchema(
                forecast_id=str(forecast.id),
                model=SapphireModelSchema(
                    key=str(model_id),
                    display_name=_display_name(stores, model_id),
                    artifact_id=str(forecast.model_artifact_id)
                    if forecast.model_artifact_id
                    else None,
                    artifact_sha256=artifact_info.artifact_sha256,
                    code_or_image_version=artifact_info.source_commit,
                    is_primary=model_id == primary_model_id,
                ),
                issued_at=forecast.issued_at,
                observation_staleness_hours=forecast.observation_staleness_hours,
                native_step_seconds=int(ensemble.time_step.total_seconds()),
                ensemble_size=ensemble.member_count,
                horizon_start=points[0].valid_time,
                horizon_end=points[-1].valid_time,
                points=points,
            )
        )
    return entries


def _day_bounds(day_start: UtcDatetime) -> UtcDatetime:
    return ensure_utc(day_start + timedelta(days=1))


def _days_covered(
    bafu_entry: BafuForecastAvailableSchema | BafuForecastUnavailableSchema,
    sapphire_entries: list[
        SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema
    ],
) -> list[UtcDatetime]:
    """D4 — the union of UTC days spanned by the available BAFU run and
    every available SAPPHIRE forecast's horizon. Empty when neither source
    has an available run for this station."""
    days: set[UtcDatetime] = set()
    if isinstance(bafu_entry, BafuForecastAvailableSchema):
        for p in bafu_entry.points:
            days.add(
                ensure_utc(
                    p.valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
                )
            )
    for entry in sapphire_entries:
        if isinstance(entry, SapphireForecastAvailableSchema):
            for p in entry.points:
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
    sapphire_entries: list[
        SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema
    ],
    *,
    day_start: UtcDatetime,
    day_end: UtcDatetime,
) -> dict[str, AlignedDailySapphireEntrySchema]:
    result: dict[str, AlignedDailySapphireEntrySchema] = {}
    for entry in sapphire_entries:
        if not isinstance(entry, SapphireForecastAvailableSchema):
            continue
        day_points = [p for p in entry.points if day_start <= p.valid_time < day_end]
        if not day_points:
            continue

        def _mean(
            field: str, pts: list[QuantileEnvelopeSchema] = day_points
        ) -> float | None:
            values = [getattr(p, field) for p in pts if getattr(p, field) is not None]
            return float(np.mean(values)) if values else None

        result[entry.model.key] = AlignedDailySapphireEntrySchema(
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
    sapphire_entries: list[
        SapphireForecastAvailableSchema | SapphireForecastUnavailableSchema
    ],
) -> list[AlignedDailyRowSchema]:
    rows: list[AlignedDailyRowSchema] = []
    for day_start in _days_covered(bafu_entry, sapphire_entries):
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
                    sapphire_entries, day_start=day_start, day_end=day_end
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
    clock: Callable[[], UtcDatetime],
) -> ForecastLabSnapshot:
    """D1 — the single assembly function. `stations` is the already
    resolved (eligible + scoped, D8/D17) set the caller wants rendered;
    this function does no HTTP, no scoping, and no station eligibility
    filtering of its own. `clock` is injected (D20) — `generated_at` and
    the `snapshot_id` derived from it are fully determined by it, never by
    `datetime.now()`."""
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

        obs_available = len(observations_section.points) > 0
        bafu_available = isinstance(bafu_entry, BafuForecastAvailableSchema)
        sapphire_available_entries = [
            e
            for e in sapphire_entries
            if isinstance(e, SapphireForecastAvailableSchema)
        ]
        sapphire_available = len(sapphire_available_entries) > 0

        obs_flags.append(obs_available)
        if observations_section.latest_available_at is not None:
            obs_times.append(ensure_utc(observations_section.latest_available_at))
        bafu_flags.append(bafu_available)
        if isinstance(bafu_entry, BafuForecastAvailableSchema):
            bafu_times.append(ensure_utc(bafu_entry.issued_at))
        sapphire_flags.append(sapphire_available)
        if sapphire_available_entries:
            sapphire_times.append(
                ensure_utc(max(e.issued_at for e in sapphire_available_entries))
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
                aligned_daily_comparison=_aligned_daily_comparison(
                    observations_section, bafu_entry, sapphire_entries
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

    snapshot_id = f"fls1-{generated_at.strftime('%Y%m%dT%H%M%SZ')}"

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
