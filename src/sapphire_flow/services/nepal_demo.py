from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.nepal_demo import (
    FORECAST_OFFSETS,
    ISSUE_OFFSETS,
    OBSERVATION_GAPS,
    OBSERVATION_OFFSETS,
    DemoIssue,
    DemoScenario,
)

if TYPE_CHECKING:
    from random import Random

    from sapphire_flow.types.datetime import UtcDatetime


def build_issue(
    *, first_issued_at: UtcDatetime, issued_at: UtcDatetime, rng: Random
) -> DemoIssue:
    # One distribution for every issue; neither index nor observations tune the draw.
    peak_hour = rng.uniform(24, 60)
    amplitude = rng.uniform(120, 280)
    width = rng.uniform(10, 18)
    baseline = rng.uniform(120, 150)
    offset = (issued_at - first_issued_at).total_seconds() / 3600
    median = tuple(
        round(
            baseline + amplitude * math.exp(-(((offset + h - peak_hour) / width) ** 2)),
            3,
        )
        for h in FORECAST_OFFSETS
    )
    return DemoIssue(
        issued_at=issued_at,
        lower=tuple(round(v * 0.78, 3) for v in median),
        median=median,
        upper=tuple(round(v * 1.27, 3) for v in median),
    )


def build_scenario(
    first_issued_at: UtcDatetime, *, observation_rng: Random, forecast_rng: Random
) -> DemoScenario:
    observations = tuple(
        None
        if any(start <= h < end for start, end in OBSERVATION_GAPS)
        else round(
            130
            + 180 * math.exp(-(((h - 32) / 12) ** 2))
            + 4 * math.sin(h / 9)
            + observation_rng.uniform(-1, 1),
            3,
        )
        for h in OBSERVATION_OFFSETS
    )
    forecasts = tuple(
        build_issue(
            first_issued_at=first_issued_at,
            issued_at=ensure_utc(first_issued_at + timedelta(hours=h)),
            rng=forecast_rng,
        )
        for h in ISSUE_OFFSETS
    )
    return DemoScenario(
        first_issued_at=first_issued_at, observations=observations, forecasts=forecasts
    )
