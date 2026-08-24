"""Plan 198 T2a — BAFU forecast archive reader for the Forecast Lab
snapshot.

Reads the quarantined `q_forecast` archive Plan 111's collector writes
(`flows/collect_bafu_forecasts.py`) and derives the newest run's normalised
p25/median/p75/min/max envelope for one station, within a lookback window.

**Pure function of a base `Path`** — no DB, no HTTP, no write. `p_forecast`
(lake level) is out of scope (a discharge comparison has no use for it).

**F3/D6 — the percentile-band normalization is the load-bearing part of
this module.** The "25.-75. Percentile" trace is a closed Plotly polygon:
``half = (n - 1) // 2`` points tracing the LOWER edge forward (ascending
``valid_time``) — this is p25 — then the UPPER edge backward (descending
``valid_time``) — this is p75 — then one closing vertex duplicating the
first point. An earlier draft of the domain comment this module's sibling
(`types/bafu_forecast.py`) carried had the two edges backwards; this
module implements the MEASURED, corrected mapping only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

_VARIANT = "q_forecast"

_TRACE_MIN = "Min / Max"  # no periods — LOWER envelope (F4)
_TRACE_MAX = "Min. / Max."  # periods — UPPER envelope (F4)
_TRACE_MEDIAN = "Median"
_TRACE_PERCENTILE_BAND = "25.-75. Percentile"

_FLAG_NON_MONOTONIC = "non_monotonic_envelope"


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuEnvelopePoint:
    valid_time: UtcDatetime
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuArchiveRun:
    station_code: str
    run_id: str
    issued_at: UtcDatetime
    inventory_produced_at: UtcDatetime
    unit: str
    native_step_seconds: int
    horizon_start: UtcDatetime
    horizon_end: UtcDatetime
    points: tuple[BafuEnvelopePoint, ...]
    quality_flags: tuple[str, ...]

    @property
    def point_count(self) -> int:
        return len(self.points)


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuRunUnavailable:
    reason: str
    message: str | None = None


BafuRunResult = BafuArchiveRun | BafuRunUnavailable


def _archive_filename_pattern(station_code: str) -> re.Pattern[str]:
    escaped = re.escape(station_code)
    return re.compile(rf"^{escaped}_{_VARIANT}_(\d{{8}}T\d{{6}}Z)\.parquet$")


def _list_run_issued_ats(base_path: Path, station_code: str) -> list[UtcDatetime]:
    parsed_dir = base_path / "parsed"
    if not parsed_dir.is_dir():
        return []
    pattern = _archive_filename_pattern(station_code)
    issued_ats: list[UtcDatetime] = []
    for path in parsed_dir.iterdir():
        match = pattern.match(path.name)
        if match is None:
            continue
        issued_ats.append(_parse_stamp(match.group(1)))
    return issued_ats


def _parse_stamp(stamp: str) -> UtcDatetime:
    from datetime import UTC, datetime

    return ensure_utc(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))


def find_latest_run_issued_at(
    base_path: Path,
    station_code: str,
    *,
    now: UtcDatetime,
    lookback: timedelta = timedelta(days=7),
) -> UtcDatetime | None:
    """The newest archived `q_forecast` run's `issued_at` for this station,
    within `[now - lookback, now]`. `None` if none is archived in that
    window (D13's "archive file missing" case, not a total failure)."""
    cutoff = ensure_utc(now - lookback)
    candidates = [
        ts
        for ts in _list_run_issued_ats(base_path, station_code)
        if cutoff <= ts <= now
    ]
    if not candidates:
        return None
    return max(candidates)


def _run_path(base_path: Path, station_code: str, issued_at: UtcDatetime) -> Path:
    stamp = issued_at.strftime("%Y%m%dT%H%M%SZ")
    return base_path / "parsed" / f"{station_code}_{_VARIANT}_{stamp}.parquet"


def _trace_series(
    frame: pl.DataFrame, trace_name: str
) -> list[tuple[UtcDatetime, float | None]]:
    rows = (
        frame.filter(pl.col("trace_name") == trace_name)
        .sort("point_index")
        .select(["valid_time", "value"])
        .iter_rows()
    )
    return [(ensure_utc(vt), val) for vt, val in rows]


def _split_percentile_band(
    frame: pl.DataFrame,
) -> (
    tuple[
        list[tuple[UtcDatetime, float | None]], list[tuple[UtcDatetime, float | None]]
    ]
    | None
):
    """Returns `(p25_series, p75_series)`, both ascending `valid_time`, or
    `None` if the polygon geometry does not reconstruct (D6)."""
    band = _trace_series(frame, _TRACE_PERCENTILE_BAND)
    n = len(band)
    if n < 3 or n % 2 == 0:
        # A closed polygon has an odd point count (2*half + 1 closing
        # vertex); anything else cannot be split into two equal halves
        # plus a closer.
        return None
    half = (n - 1) // 2
    forward = band[:half]
    backward_raw = band[half : half * 2]
    backward = list(reversed(backward_raw))
    forward_times = [t for t, _ in forward]
    backward_times = [t for t, _ in backward]
    if forward_times != backward_times:
        return None
    return forward, backward


def _monotonic_ok(point: BafuEnvelopePoint) -> bool:
    values = [point.minimum, point.p25, point.median, point.p75, point.maximum]
    known = [v for v in values if v is not None]
    return all(a <= b for a, b in zip(known, known[1:], strict=False))


def _native_step_seconds(times: list[UtcDatetime]) -> int:
    if len(times) < 2:
        return 3600
    diffs = [
        int((b - a).total_seconds()) for a, b in zip(times, times[1:], strict=False)
    ]
    return max(set(diffs), key=diffs.count)


def read_latest_bafu_run(
    base_path: Path,
    station_code: str,
    *,
    now: UtcDatetime,
    lookback: timedelta = timedelta(days=7),
) -> BafuRunResult:
    issued_at = find_latest_run_issued_at(
        base_path, station_code, now=now, lookback=lookback
    )
    if issued_at is None:
        return BafuRunUnavailable(
            reason="no_matching_run",
            message=(
                f"no {_VARIANT} run archived for station {station_code} in the "
                f"last {lookback}"
            ),
        )

    path = _run_path(base_path, station_code, issued_at)
    try:
        frame = pl.read_parquet(path)
    except (OSError, pl.exceptions.ComputeError) as exc:
        log.warning(
            "forecast_lab.bafu_archive_read_failed",
            station_code=station_code,
            path=str(path),
            error=str(exc),
        )
        return BafuRunUnavailable(reason="unreadable_archive_file", message=str(exc))

    if frame.is_empty():
        return BafuRunUnavailable(reason="empty_archive_file")

    split = _split_percentile_band(frame)
    if split is None:
        return BafuRunUnavailable(
            reason="parse_error",
            message=(
                "the 25.-75. Percentile polygon does not reconstruct into two "
                "matching ascending valid_time halves"
            ),
        )
    p25_series, p75_series = split
    median_series = _trace_series(frame, _TRACE_MEDIAN)
    minimum_series = _trace_series(frame, _TRACE_MIN)
    maximum_series = _trace_series(frame, _TRACE_MAX)

    canonical_times = [t for t, _ in p25_series]
    for name, series in (
        ("Median", median_series),
        (_TRACE_MIN, minimum_series),
        (_TRACE_MAX, maximum_series),
    ):
        if [t for t, _ in series] != canonical_times:
            return BafuRunUnavailable(
                reason="parse_error",
                message=(
                    f"{name!r} trace valid_time sequence does not match the "
                    "percentile-band halves"
                ),
            )

    p25_by_time = dict(p25_series)
    p75_by_time = dict(p75_series)
    median_by_time = dict(median_series)
    min_by_time = dict(minimum_series)
    max_by_time = dict(maximum_series)

    points: list[BafuEnvelopePoint] = []
    any_non_monotonic = False
    for t in canonical_times:
        point = BafuEnvelopePoint(
            valid_time=t,
            minimum=min_by_time[t],
            p25=p25_by_time[t],
            median=median_by_time[t],
            p75=p75_by_time[t],
            maximum=max_by_time[t],
        )
        if not _monotonic_ok(point):
            any_non_monotonic = True
        points.append(point)

    quality_flags: tuple[str, ...] = (_FLAG_NON_MONOTONIC,) if any_non_monotonic else ()

    unit_rows = frame.filter(pl.col("trace_name") == _TRACE_MIN)["unit"].to_list()
    unit = unit_rows[0] if unit_rows else "m³/s"

    return BafuArchiveRun(
        station_code=station_code,
        run_id=f"{station_code}_{_VARIANT}_{issued_at.strftime('%Y%m%dT%H%M%SZ')}",
        issued_at=issued_at,
        inventory_produced_at=ensure_utc(frame["produced_at"][0]),
        unit=unit,
        native_step_seconds=_native_step_seconds(canonical_times),
        horizon_start=canonical_times[0],
        horizon_end=canonical_times[-1],
        points=tuple(points),
        quality_flags=quality_flags,
    )
