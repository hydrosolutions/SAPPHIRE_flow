from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Literal

from sapphire_flow.services.network_overview import (
    empirical_quantile_type7,
    reduce_forecast_point,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.time_grid import PeriodLabel

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast

PeakMethod = Literal["stored_quantile", "member_median", "member_type7"]
PeakReason = Literal[
    "parameter_not_supported",
    "units_not_supported",
    "forecast_qc_rejected",
    "invalid_series",
    "no_remaining_support",
    "percentile_missing",
    "insufficient_members",
]


class PeakQuantile(Enum):
    MEDIAN = 0.5
    UPPER = 0.9


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastPeak:
    quantile: PeakQuantile
    method: PeakMethod
    reason: PeakReason | None = None
    discharge: float | None = None
    valid_time: UtcDatetime | None = None
    support_start: UtcDatetime | None = None
    support_end: UtcDatetime | None = None
    member_count: int | None = None

    def __post_init__(self) -> None:
        if self.discharge is None:
            if self.reason is None:
                raise ValueError("unavailable peak requires a reason")
        elif (
            not math.isfinite(self.discharge)
            or self.discharge < 0
            or self.reason is not None
            or self.valid_time is None
        ):
            raise ValueError(
                "available peak requires finite discharge and a valid time"
            )


def remaining_forecast_peaks(
    forecast: OperationalForecast, *, as_of: UtcDatetime
) -> tuple[ForecastPeak, ForecastPeak]:
    """Maxima of pointwise discharge quantiles over remaining stored support."""
    ensemble = forecast.ensemble
    stored = ensemble.representation is EnsembleRepresentation.QUANTILES
    methods: tuple[PeakMethod, PeakMethod] = (
        ("stored_quantile", "stored_quantile")
        if stored
        else ("member_median", "member_type7")
    )

    def unavailable(reason: PeakReason) -> tuple[ForecastPeak, ForecastPeak]:
        return (
            ForecastPeak(
                quantile=PeakQuantile.MEDIAN, method=methods[0], reason=reason
            ),
            ForecastPeak(quantile=PeakQuantile.UPPER, method=methods[1], reason=reason),
        )

    if ensemble.parameter != "discharge":
        return unavailable("parameter_not_supported")
    if ensemble.units not in {"m3/s", "m³/s"}:
        return unavailable("units_not_supported")
    if forecast.qc_status in {
        QcStatus.QC_FAILED,
        QcStatus.QC_SUSPECT,
        QcStatus.MISSING,
    }:
        return unavailable("forecast_qc_rejected")
    if ensemble.time_step <= timedelta(0):
        return unavailable("invalid_series")
    key = "quantile" if stored else "member_id"
    grouped: dict[UtcDatetime, list[tuple[float, float]]] = {}
    period = ensemble.period_definition
    peaks: list[ForecastPeak | None] = [None, None]
    reasons: list[PeakReason | None] = [None, None]
    try:
        for timestamp, coordinate, value in ensemble.values.select(
            "valid_time", key, "value"
        ).iter_rows():
            grouped.setdefault(ensure_utc(timestamp), []).append(
                (float(coordinate), float(value))
            )
        if len(grouped) > 512:
            return unavailable("invalid_series")
        for timestamp, keyed in sorted(grouped.items()):
            start = end = None
            if period is not None:
                start = ensure_utc(
                    timestamp
                    - (
                        period.grid.step
                        if period.label is PeriodLabel.END
                        else timedelta(0)
                    )
                )
                end = ensure_utc(start + period.grid.step)
            if (end is not None and end <= as_of) or (
                end is None and timestamp < as_of
            ):
                continue
            values, point_reasons = _point_quantiles(
                timestamp, keyed, ensemble.representation
            )
            for index, (quantile, value) in enumerate(
                zip(PeakQuantile, values, strict=True)
            ):
                reasons[index] = reasons[index] or point_reasons[index]
                previous = peaks[index]
                if value is not None and (
                    previous is None
                    or previous.discharge is None
                    or value > previous.discharge
                ):
                    peaks[index] = ForecastPeak(
                        quantile=quantile,
                        method=methods[index],
                        discharge=value,
                        valid_time=timestamp,
                        support_start=start,
                        support_end=end,
                        member_count=None if stored else len(keyed),
                    )
    except (TypeError, ValueError, OverflowError):
        return unavailable("invalid_series")
    results = [
        ForecastPeak(
            quantile=quantile,
            method=methods[index],
            reason=reasons[index] or "no_remaining_support",
        )
        if reasons[index] is not None or peaks[index] is None
        else peaks[index]
        for index, quantile in enumerate(PeakQuantile)
    ]
    median_peak, upper_peak = results
    assert median_peak is not None and upper_peak is not None
    return median_peak, upper_peak


def _point_quantiles(
    timestamp: UtcDatetime,
    keyed: list[tuple[float, float]],
    representation: EnsembleRepresentation,
) -> tuple[
    tuple[float | None, float | None], tuple[PeakReason | None, PeakReason | None]
]:
    keys = [key for key, _ in keyed]
    values = [value for _, value in keyed]
    if (
        len(set(keys)) != len(keys)
        or not values
        or any(not math.isfinite(key) for key in keys)
        or any(not math.isfinite(value) or value < 0 for value in values)
    ):
        raise ValueError("invalid forecast series")
    if representation is EnsembleRepresentation.QUANTILES:
        ordered = sorted(keyed)
        if any(not 0 <= key <= 1 for key in keys) or any(
            left[1] > right[1]
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("invalid quantile ordering")
        levels = dict(keyed)
        return (levels.get(0.5), levels.get(0.9)), (
            None if 0.5 in levels else "percentile_missing",
            None if 0.9 in levels else "percentile_missing",
        )
    middle = reduce_forecast_point(
        timestamp, keyed, EnsembleRepresentation.MEMBERS, "discharge"
    ).median
    upper = empirical_quantile_type7(values, 0.9) if len(values) >= 5 else None
    if upper is not None and not math.isfinite(upper):
        raise ValueError("nonfinite empirical quantile")
    return (middle, upper), (
        None,
        None if upper is not None else "insufficient_members",
    )
