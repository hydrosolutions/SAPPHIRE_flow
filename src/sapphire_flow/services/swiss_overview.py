from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import timedelta
from statistics import median
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.exceptions import DailyAuthorityIntegrityError
from sapphire_flow.services.evaluation_forecasts import (
    EvaluationStores,
    daily_candidate_from_record,
    daily_day_available,
    select_current_daily,
)
from sapphire_flow.services.forecast_lab.bafu_archive import (
    BafuArchiveRun,
    BafuRunUnavailable,
    BafuUnitPolicy,
    parse_archive_stamp,
    read_bafu_run,
)
from sapphire_flow.services.forecast_read import require_standard_forecast_store
from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
from sapphire_flow.types.daily_forecast_claim import DailyClaimKey
from sapphire_flow.types.daily_forecast_outcome import DailyOutputSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ForecastId, RejectedForecastId
from sapphire_flow.types.swiss_overview import HeadlineAbsence, MedianPeak

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from sapphire_flow.types.daily_forecast_snapshot import DailyDaySnapshot
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.evaluation_forecast import (
        EvaluationCandidate,
        EvaluationReadSettings,
    )
    from sapphire_flow.types.ids import StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class CurrentHeadline:
    candidate: EvaluationCandidate | None
    absence: HeadlineAbsence | None


def read_current_headlines(
    *,
    station_ids: Sequence[StationId],
    settings: EvaluationReadSettings,
    stores: EvaluationStores,
    as_of: UtcDatetime,
) -> Mapping[StationId, CurrentHeadline]:
    require_standard_forecast_store(stores.forecasts)
    require_standard_forecast_store(stores.rejected)
    for group_id in sorted(settings.group_ids, key=str):
        group = stores.groups.fetch_group(group_id)
        if (
            group is None
            or group.id != group_id
            or group.tenant_id != settings.tenant_id
        ):
            raise DailyAuthorityIntegrityError(
                "evaluation group is outside configured tenant"
            )
    days: dict[DailyClaimKey, DailyDaySnapshot] = {}
    for group_id in sorted(settings.group_ids, key=str):
        key = DailyClaimKey(
            tenant_id=settings.tenant_id,
            deployment=settings.deployment,
            group_id=group_id,
            model_id=settings.daily_model_id,
            context=ScheduledDailyForecast(scheduled_tick=as_of),
        )
        day = stores.daily.read_day(key)
        daily_day_available(day, key, as_of)
        days[key] = day
    scope = frozenset(station_ids)
    references = [
        (day.authoritative, ref)
        for key, day in days.items()
        if daily_day_available(day, key, as_of) and day.authoritative is not None
        for member in day.authoritative.outcomes or ()
        if member.station_id in scope
        for ref in member.references
    ]
    accepted = stores.forecasts.fetch_forecasts_by_ids(
        list(
            dict.fromkeys(
                ForecastId(ref.output_id)
                for _, ref in references
                if ref.source is DailyOutputSource.FORECAST
            )
        )
    )
    rejected = stores.rejected.fetch_rejected_forecasts_by_ids(
        list(
            dict.fromkeys(
                RejectedForecastId(ref.output_id)
                for _, ref in references
                if ref.source is DailyOutputSource.REJECTED
            )
        )
    )
    candidates = {
        (summary.claim, ref): daily_candidate_from_record(
            summary=summary,
            ref=ref,
            record=accepted.get(ForecastId(ref.output_id))
            if ref.source is DailyOutputSource.FORECAST
            else rejected.get(RejectedForecastId(ref.output_id)),
            now=as_of,
            settings=settings,
        )
        for summary, ref in references
    }
    result: dict[StationId, CurrentHeadline] = {}
    for sid in station_ids:
        selected = select_current_daily(
            station_id=sid,
            parameters=settings.parameters,
            days=days,
            candidates=candidates,
            now=as_of,
            valid_at=as_of,
        )
        candidate = selected.candidates.get("discharge")
        absence = None
        if candidate is None:
            absence = (
                HeadlineAbsence.EXPIRED_HORIZON
                if "discharge" in selected.uncovered
                else HeadlineAbsence.NO_CURRENT_CANDIDATE
            )
            if not any(day.authoritative is not None for day in days.values()):
                absence = HeadlineAbsence.PENDING_CURRENT_ISSUE
        result[sid] = CurrentHeadline(candidate=candidate, absence=absence)
    return result


def median_peak(
    points: Sequence[tuple[UtcDatetime, float]],
    *,
    step: timedelta,
    as_of: UtcDatetime,
) -> MedianPeak | None:
    if step <= timedelta(0) or any(not math.isfinite(v) for _, v in points):
        return None
    valid = sorted((t, value) for t, value in points if t + step > as_of)
    if not valid:
        return None
    peak_time, peak_value = max(
        valid, key=lambda point: (point[1], -point[0].timestamp())
    )
    return MedianPeak(
        value=peak_value,
        valid_time=peak_time,
        horizon_start=valid[0][0],
        horizon_end=ensure_utc(valid[-1][0] + step),
    )


def candidate_has_median(candidate: EvaluationCandidate) -> bool:
    record = candidate.record
    if record.representation is not EnsembleRepresentation.QUANTILES:
        return True
    if isinstance(record, OperationalForecast):
        return 0.5 in record.ensemble.values["quantile"].to_list()
    return any(float(key) == 0.5 for key in record.values)


def candidate_peak(
    candidate: EvaluationCandidate, *, as_of: UtcDatetime
) -> MedianPeak | None:
    record = candidate.record
    series: dict[UtcDatetime, list[float]] = {}
    if isinstance(record, OperationalForecast):
        ensemble = record.ensemble
        step = ensemble.time_step
        for row in ensemble.values.iter_rows(named=True):
            if (
                ensemble.representation is EnsembleRepresentation.QUANTILES
                and row["quantile"] != 0.5
            ):
                continue
            series.setdefault(row["valid_time"], []).append(row["value"])
    else:
        step = timedelta(seconds=record.time_step_seconds)
        for key, values in record.values.items():
            if (
                record.representation is EnsembleRepresentation.QUANTILES
                and float(key) != 0.5
            ):
                continue
            for time, value in values:
                series.setdefault(time, []).append(value)
    return median_peak(
        [(time, median(values)) for time, values in series.items()],
        step=step,
        as_of=as_of,
    )


def read_bafu_headlines(
    base_path: Path | None,
    station_codes: Sequence[str],
    *,
    as_of: UtcDatetime,
) -> Mapping[str, tuple[BafuArchiveRun, MedianPeak] | HeadlineAbsence]:
    result: dict[str, tuple[BafuArchiveRun, MedianPeak] | HeadlineAbsence] = (
        dict.fromkeys(station_codes, HeadlineAbsence.MISSING_ARCHIVE)
    )
    if base_path is None:
        return result
    selected: dict[str, UtcDatetime] = {}
    pattern = re.compile(r"^(.+)_q_forecast_(\d{8}T\d{6}Z)\.parquet$")
    cutoff_stamp = (as_of - timedelta(days=7)).strftime("%Y%m%dT%H%M%SZ")
    latest_stamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    try:
        for path in (base_path / "parsed").iterdir():
            match = pattern.fullmatch(path.name)
            if match is None or match[1] not in result:
                continue
            if not cutoff_stamp <= match[2] <= latest_stamp:
                continue
            try:
                issue = parse_archive_stamp(match[2])
            except ValueError:
                # Invalid filenames are not runs; never open or select them.
                continue
            if as_of - timedelta(days=7) <= issue <= as_of:
                selected[match[1]] = max(issue, selected.get(match[1], issue))
    except FileNotFoundError:
        return result
    except OSError:
        return dict.fromkeys(station_codes, HeadlineAbsence.SOURCE_READ_FAILED)
    for code, issue in selected.items():
        try:
            run = read_bafu_run(
                base_path,
                code,
                issued_at=issue,
                unit_policy=BafuUnitPolicy.EXPLICIT_DISCHARGE,
            )
        except (ValueError, TypeError, KeyError, pl.exceptions.PolarsError):
            result[code] = HeadlineAbsence.INVALID_SOURCE_DATA
            continue
        if isinstance(run, BafuRunUnavailable):
            result[code] = (
                HeadlineAbsence.SOURCE_READ_FAILED
                if run.reason == "unreadable_archive_file"
                else HeadlineAbsence.INVALID_SOURCE_DATA
            )
            continue
        if (
            run.inventory_produced_at > as_of
            or run.unit not in {"m³/s", "m3/s"}
            or run.quality_flags
            or not run.points
            or any(p.median is None for p in run.points)
        ):
            result[code] = HeadlineAbsence.INVALID_SOURCE_DATA
            continue
        peak = median_peak(
            [(p.valid_time, p.median) for p in run.points if p.median is not None],
            step=timedelta(seconds=run.native_step_seconds),
            as_of=as_of,
        )
        result[code] = (
            (run, peak) if peak is not None else HeadlineAbsence.EXPIRED_HORIZON
        )
    return result
