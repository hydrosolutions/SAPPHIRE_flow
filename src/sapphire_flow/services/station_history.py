from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.station_history import (
    HistoryCounts,
    HistoryOutcome,
    HistoryPoint,
    HistoryResolution,
    HistorySeries,
    StationHistoryResponse,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sapphire_flow.types.station_history import (
        HistoryCurve,
        HistorySample,
        HistorySource,
        StationHistoryRequest,
    )


def _counts(rows: tuple[HistorySample, ...]) -> HistoryCounts:
    counts = Counter(row.outcome for row in rows)
    return HistoryCounts(
        source=len(rows),
        accepted=counts[HistoryOutcome.ACCEPTED],
        rejected=counts[HistoryOutcome.REJECTED],
        held=counts[HistoryOutcome.HELD],
        rejected_source_qc=sum(r.reason == "source_qc_rejected" for r in rows),
    )


def _point(
    rows: tuple[HistorySample, ...],
    timestamp: datetime,
    end: datetime | None,
    interval: Literal["native", "complete_hour", "clipped_hour", "unfinished_hour"],
) -> HistoryPoint:
    values = [row.value for row in rows if row.value is not None]
    stored = [row.stored_at for row in rows if row.stored_at is not None]
    counts = _counts(rows)
    return HistoryPoint(
        timestamp=timestamp,
        end=end,
        value=mean(values) if values else None,
        minimum=min(values) if values else None,
        maximum=max(values) if values else None,
        sample_count=len(values),
        counts=counts,
        outcome=rows[0].outcome if interval == "native" else None,
        stored_at=rows[0].stored_at if interval == "native" else None,
        completeness="partial" if counts.rejected or counts.held else "complete",
        interval=interval,
        source_qc_status=rows[0].source_qc_status if interval == "native" else None,
        source_assessment=rows[0].source_assessment if interval == "native" else None,
        source_qc_counts=dict(Counter(row.source_qc_status.value for row in rows)),
        source_assessment_counts=dict(Counter(row.source_assessment for row in rows)),
        warnings=tuple(sorted({w for row in rows for w in row.warnings})),
        reasons=tuple(sorted({row.reason for row in rows if row.reason})),
        curve_refs=tuple(
            sorted({row.curve.content_sha256 for row in rows if row.curve})
        ),
        policies=tuple(sorted({row.policy for row in rows if row.policy})),
        stored_at_min=min(stored) if stored else None,
        stored_at_max=max(stored) if stored else None,
    )


def build_station_history(
    request: StationHistoryRequest,
    samples: tuple[HistorySample, ...],
) -> StationHistoryResponse:
    groups: dict[tuple[HistorySource, ForecastDataUse], list[HistorySample]] = {}
    if ForecastDataUse.STANDARD in request.purposes:
        groups["measured", ForecastDataUse.STANDARD] = []
        groups["supplied", ForecastDataUse.STANDARD] = []
    if ForecastDataUse.PROVISIONAL_RATING in request.purposes:
        groups["rating_derived", ForecastDataUse.PROVISIONAL_RATING] = []
    curves: dict[str, HistoryCurve] = {}
    for sample in samples:
        if not request.start <= sample.timestamp < request.end:
            raise ValueError("history sample outside requested interval")
        key = sample.source, sample.data_use
        if key not in groups:
            raise ValueError("history sample outside requested purpose")
        groups[key].append(sample)
        if sample.curve:
            digest = sample.curve.content_sha256
            if digest in curves and curves[digest] != sample.curve:
                raise ValueError("history curve digest disagreement")
            curves[digest] = sample.curve
    series: list[HistorySeries] = []
    for (source, purpose), group in groups.items():
        rows = tuple(sorted(group, key=lambda row: row.timestamp))
        counts = _counts(rows)
        numeric_times = [row.timestamp for row in rows if row.value is not None]
        series.append(
            HistorySeries(
                source=source,
                data_use=purpose,
                counts=counts,
                availability="available"
                if counts.accepted
                else "no_eligible_result"
                if counts.source
                else "no_source",
                completeness="partial"
                if counts.rejected or counts.held
                else "complete",
                latest_timestamp=max(numeric_times) if numeric_times else None,
                points=_points(request, rows),
            )
        )
    return StationHistoryResponse(
        station_id=str(request.station_id),
        start=request.start,
        end=request.end,
        generated_at=request.generated_at,
        resolution=request.resolution,
        aggregation="native"
        if request.resolution is HistoryResolution.NATIVE
        else "sample_mean",
        series=tuple(series),
        curves=curves,
    )


def _points(
    request: StationHistoryRequest,
    rows: tuple[HistorySample, ...],
) -> tuple[HistoryPoint, ...]:
    if request.resolution is HistoryResolution.NATIVE:
        return tuple(_point((row,), row.timestamp, None, "native") for row in rows)
    buckets: dict[datetime, list[HistorySample]] = defaultdict(list)
    for row in rows:
        buckets[row.timestamp.replace(minute=0, second=0, microsecond=0)].append(row)
    hour = request.start.replace(minute=0, second=0, microsecond=0)
    points: list[HistoryPoint] = []
    while hour < request.end:
        next_hour = hour + timedelta(hours=1)
        start, end = max(hour, request.start), min(next_hour, request.end)
        interval: Literal["complete_hour", "clipped_hour", "unfinished_hour"] = (
            "complete_hour"
        )
        if next_hour > request.generated_at:
            interval = "unfinished_hour"
        elif start != hour or end != next_hour:
            interval = "clipped_hour"
        points.append(_point(tuple(buckets[hour]), start, end, interval))
        hour = next_hour
    return tuple(points)
