from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.network_overview import (
    OverviewAvailability,
    OverviewFreshness,
    OverviewFreshnessResult,
    OverviewGap,
    OverviewPoint,
    OverviewSeries,
    QuartileMethod,
)
from sapphire_flow.types.time_grid import PeriodLabel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.network_overview import OverviewFreshnessPolicy


def empirical_quantile_type7(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    fraction = position - lower
    if fraction == 0:
        return ordered[lower]
    return (1 - fraction) * ordered[lower] + fraction * ordered[lower + 1]


def reduce_forecast_point(
    timestamp: UtcDatetime,
    keyed: list[tuple[float, float]],
    representation: EnsembleRepresentation,
    parameter: str,
) -> OverviewPoint:
    keys = [key for key, _ in keyed]
    values = [value for _, value in keyed]
    if len(set(keys)) != len(keys) or any(not math.isfinite(v) for v in values):
        raise ValueError("invalid or duplicate forecast values")
    if any(not math.isfinite(key) for key in keys):
        raise ValueError("invalid forecast coordinate")
    if representation is EnsembleRepresentation.QUANTILES:
        if any(not 0 <= key <= 1 for key in keys):
            raise ValueError("invalid quantile coordinate")
        levels = dict(keyed)
        if 0.5 not in levels:
            raise ValueError("missing median")
        middle, lower, upper = levels[0.5], levels.get(0.25), levels.get(0.75)
    else:
        middle = median(values)
        lower = empirical_quantile_type7(values, 0.25) if len(values) >= 5 else None
        upper = empirical_quantile_type7(values, 0.75) if len(values) >= 5 else None
    if (
        any(
            not math.isfinite(value)
            for value in (middle, lower, upper)
            if value is not None
        )
        or (parameter == "discharge" and any(value < 0 for value in values))
        or (lower is not None and lower > middle)
        or (upper is not None and upper < middle)
    ):
        raise ValueError("invalid forecast bounds")
    return OverviewPoint(valid_time=timestamp, median=middle, p25=lower, p75=upper)


def compact_forecast_series(
    ensemble: ForecastEnsemble, *, as_of: UtcDatetime, point_budget: int = 24
) -> OverviewSeries:
    if point_budget < 3:
        raise ValueError("point budget must be at least three")
    invalid = OverviewSeries(availability=OverviewAvailability.INVALID_SERIES)
    if ensemble.time_step <= timedelta(0):
        return invalid
    label_offset = (
        ensemble.time_step
        if ensemble.period_definition is not None
        and ensemble.period_definition.label is PeriodLabel.END
        else timedelta(0)
    )
    key = (
        "quantile"
        if ensemble.representation is EnsembleRepresentation.QUANTILES
        else "member_id"
    )
    grouped: dict[UtcDatetime, list[tuple[float, float]]] = {}
    try:
        for row in ensemble.values.select("valid_time", key, "value").iter_rows():
            timestamp = ensure_utc(row[0])
            grouped.setdefault(timestamp, []).append((float(row[1]), float(row[2])))
        if len(grouped) > 512:
            raise ValueError("forecast support exceeds admission bound")
        points = tuple(
            reduce_forecast_point(
                timestamp, keyed, ensemble.representation, ensemble.parameter
            )
            for timestamp, keyed in sorted(grouped.items())
            if timestamp + ensemble.time_step - label_offset > as_of
        )
    except (TypeError, ValueError, OverflowError):
        return invalid
    if not points:
        return OverviewSeries(availability=OverviewAvailability.EXPIRED)
    peak = max(points, key=lambda point: point.median)
    anchors = {0, len(points) - 1, points.index(peak)}
    gaps: list[OverviewGap] = []
    for index in range(1, len(points)):
        previous_end = ensure_utc(points[index - 1].valid_time + ensemble.time_step)
        if points[index].valid_time < previous_end:
            return invalid
        if points[index].valid_time > previous_end:
            anchors.update((index - 1, index))
            gaps.append(
                OverviewGap(
                    start=ensure_utc(previous_end - label_offset),
                    end=ensure_utc(points[index].valid_time - label_offset),
                )
            )
    available = len(anchors) <= point_budget
    if available:
        if len(points) <= point_budget:
            anchors = set(range(len(points)))
        else:
            offsets = [
                (point.valid_time - points[0].valid_time).total_seconds()
                for point in points
            ]
            for position in range(1, point_budget - 1):
                if len(anchors) >= point_budget:
                    break
                target = offsets[-1] * position / (point_budget - 1)
                remaining = (
                    index for index in range(len(points)) if index not in anchors
                )
                anchors.add(
                    min(
                        remaining,
                        key=lambda index: (abs(offsets[index] - target), index),
                    )
                )
    return OverviewSeries(
        availability=(
            OverviewAvailability.AVAILABLE
            if available
            else OverviewAvailability.COMPACT_UNAVAILABLE
        ),
        points=tuple(points[i] for i in sorted(anchors)) if available else (),
        peak=peak,
        support_start=ensure_utc(points[0].valid_time - label_offset),
        support_end=ensure_utc(
            points[-1].valid_time + ensemble.time_step - label_offset
        ),
        gaps=tuple(gaps),
        original_point_count=len(points),
        quartile_method=(
            QuartileMethod.STORED
            if ensemble.representation is EnsembleRepresentation.QUANTILES
            else QuartileMethod.MEMBERS
        ),
    )


def forecast_freshness(
    *,
    issued_at: UtcDatetime,
    as_of: UtcDatetime,
    policy: OverviewFreshnessPolicy | None,
) -> OverviewFreshnessResult:
    if policy is None:
        return OverviewFreshnessResult(freshness=OverviewFreshness.UNKNOWN)
    local_day = as_of.astimezone(policy.timezone).date()
    expected = ensure_utc(
        datetime.combine(local_day, policy.issue_time, policy.issue_timezone)
    )
    deadline = ensure_utc(
        datetime.combine(local_day, policy.delivery_deadline, policy.timezone)
    )
    if deadline < expected:
        deadline = ensure_utc(deadline + timedelta(days=1))
    previous_issue = ensure_utc(
        datetime.combine(
            local_day - timedelta(days=1), policy.issue_time, policy.issue_timezone
        )
    )
    threshold = expected if as_of >= deadline else previous_issue
    return OverviewFreshnessResult(
        freshness=(
            OverviewFreshness.CURRENT
            if issued_at >= threshold
            else OverviewFreshness.STALE
        ),
        expected_issue=expected,
        deadline=deadline,
    )
