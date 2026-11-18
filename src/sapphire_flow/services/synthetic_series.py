"""Plan 540 M3d-1: pure seeded synthetic discharge series (no clock, no I/O).

Baseflow on an exponential recession plus gamma-shaped storm pulses. No noise,
no bare sine, no clipping: the frozen-sensor rule must never see a flat run.
Limits come from the passed-in resolved ``QcRuleSet``; amplitudes shrink
deterministically until the series sits inside them.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet

PARAMETER = "discharge"
_SECONDS_PER_DAY = 86400.0
_MAX_SHRINKS = 40
_SHRINK = 0.8


class TimeLabel(Enum):
    UTC_INSTANT = auto()
    LOCAL_DAY = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class StormParams:
    base_m3s: float
    base_decay_days: float
    storm_peak_m3s: float
    storm_rise_days: float
    storm_shape: float
    storms_per_30_days: float


@dataclass(frozen=True, kw_only=True, slots=True)
class SeriesSpec:
    seed: int
    step: timedelta
    n_points: int
    last: UtcDatetime
    label: TimeLabel
    params: StormParams


HOURLY_SPEC = SeriesSpec(
    seed=540,
    step=timedelta(hours=1),
    n_points=72,
    last=ensure_utc(datetime(2025, 1, 2, 23, tzinfo=UTC)),
    label=TimeLabel.UTC_INSTANT,
    params=StormParams(
        base_m3s=18.0,
        base_decay_days=40.0,
        storm_peak_m3s=14.0,
        storm_rise_days=0.75,
        storm_shape=2.0,
        storms_per_30_days=15.0,
    ),
)
DAILY_SPEC = SeriesSpec(
    seed=541,
    step=timedelta(days=1),
    n_points=90,
    last=ensure_utc(datetime(2025, 1, 2, tzinfo=UTC)),
    label=TimeLabel.LOCAL_DAY,
    params=StormParams(
        base_m3s=18.0,
        base_decay_days=60.0,
        storm_peak_m3s=14.0,
        storm_rise_days=6.0,
        storm_shape=2.0,
        storms_per_30_days=2.5,
    ),
)


def _kernel(tau_days: float, rise_days: float, shape: float) -> float:
    if tau_days <= 0.0:
        return 0.0
    x = tau_days / rise_days
    return (x**shape) * math.exp(shape * (1.0 - x))


def _within_limits(
    values: list[float], thresholds: dict[str, dict[str, float]]
) -> bool:
    rng_t = thresholds.get("range_check", {})
    roc = thresholds.get("rate_of_change", {}).get("max_rate")
    spike = thresholds.get("spike", {})
    low = rng_t.get("value_min")
    high = rng_t.get("value_max")
    if any(
        (low is not None and v < low) or (high is not None and v > high) for v in values
    ):
        return False
    steps = list(zip(values, values[1:], strict=False))
    if roc is not None and any(abs(b - a) > roc for a, b in steps):
        return False
    triples = zip(values, values[1:], values[2:], strict=False)
    if "max_delta" in spike:
        limit = spike["max_delta"]
        return not any(abs(v - p) > limit and abs(v - n) > limit for p, v, n in triples)
    if "tolerance" in spike:
        tol = spike["tolerance"]
        return not any(
            abs(v - p) > tol * abs(p) and abs(v - n) > tol * abs(p)
            for p, v, n in triples
        )
    return True


def _thresholds(rule_set: QcRuleSet, step: timedelta) -> dict[str, dict[str, float]]:
    rules = rule_set.rules_for(PARAMETER, step, network=None)
    if not rules:
        raise ValueError(f"rule set has no {PARAMETER} rules for step {step}")
    return {r.rule_id: r.thresholds for r in rules}


def synthetic_discharge(
    *,
    rng: random.Random,
    rule_set: QcRuleSet,
    step: timedelta,
    n_points: int,
    params: StormParams,
) -> list[float]:
    thresholds = _thresholds(rule_set, step)
    step_days = step.total_seconds() / _SECONDS_PER_DAY
    span_days = n_points * step_days
    n_storms = max(1, round(params.storms_per_30_days * span_days / 30.0))
    lead = 3.0 * params.storm_rise_days
    storms = [
        (
            rng.uniform(-lead, span_days),
            rng.uniform(0.5, 1.0) * params.storm_peak_m3s,
        )
        for _ in range(n_storms)
    ]
    for shrink in range(_MAX_SHRINKS):
        scale = _SHRINK**shrink
        values = [
            params.base_m3s * math.exp(-i * step_days / params.base_decay_days)
            + scale
            * sum(
                amp
                * _kernel(
                    i * step_days - onset,
                    params.storm_rise_days,
                    params.storm_shape,
                )
                for onset, amp in storms
            )
            for i in range(n_points)
        ]
        if _within_limits(values, thresholds):
            return values
    raise ValueError("no synthetic series fits the QC limits of this rule set")


def series_timestamps(
    *, last: UtcDatetime, step: timedelta, n_points: int
) -> list[UtcDatetime]:
    return [ensure_utc(last - step * (n_points - 1 - i)) for i in range(n_points)]


def render_csv(
    timestamps: list[UtcDatetime], values: list[float], label: TimeLabel
) -> str:
    def stamp(t: UtcDatetime) -> str:
        if label is TimeLabel.LOCAL_DAY:
            return t.strftime("%Y-%m-%d")
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = [f"{stamp(t)},{v:.6f}" for t, v in zip(timestamps, values, strict=True)]
    return "\n".join(["time,value", *rows]) + "\n"


def render_scenario(spec: SeriesSpec, rule_set: QcRuleSet) -> str:
    values = synthetic_discharge(
        rng=random.Random(spec.seed),
        rule_set=rule_set,
        step=spec.step,
        n_points=spec.n_points,
        params=spec.params,
    )
    stamps = series_timestamps(last=spec.last, step=spec.step, n_points=spec.n_points)
    return render_csv(stamps, values, spec.label)
