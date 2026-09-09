from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.services.training_data import (
    expected_past_buckets,
    floor_to_time_step,
    missing_buckets,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import InputQualityFlag, aggregate_input_quality

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import timedelta

    import polars as pl

    from sapphire_flow.config.deployment import InputQualityConfig
    from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import (
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
    WarmUpSource,
)


def assess_input_quality(
    *,
    observation_staleness_hours: float | None,
    warm_up_source: WarmUpSource | None,
    warm_up_state_age_hours: float | None,
    nwp_cycle_source: NwpCycleSource,
    nwp_age_hours: float,
    obs_partial_hours: float,
    config: InputQualityConfig,
    warmup_partial_hours: float,
    warmup_degraded_hours: float,
) -> tuple[InputQualityLevel, tuple[InputQualityFlag, ...]]:
    if obs_partial_hours >= config.obs_degraded_hours:
        raise ValueError(
            f"obs_partial_hours ({obs_partial_hours}) must be less than "
            f"obs_degraded_hours ({config.obs_degraded_hours})"
        )
    if warmup_partial_hours >= warmup_degraded_hours:
        raise ValueError(
            f"warmup_partial_hours ({warmup_partial_hours}) must be less than "
            f"warmup_degraded_hours ({warmup_degraded_hours})"
        )

    flags: list[InputQualityFlag] = []

    # Observation staleness
    if observation_staleness_hours is not None:
        if observation_staleness_hours >= config.obs_degraded_hours:
            flags.append(
                InputQualityFlag(
                    category=InputQualityCategory.OBSERVATION,
                    level=InputQualityLevel.DEGRADED,
                    detail=(
                        f"Observations {observation_staleness_hours:.1f}h stale "
                        f"(threshold: {config.obs_degraded_hours:.1f}h)"
                    ),
                )
            )
        elif observation_staleness_hours >= obs_partial_hours:
            flags.append(
                InputQualityFlag(
                    category=InputQualityCategory.OBSERVATION,
                    level=InputQualityLevel.PARTIAL,
                    detail=(
                        f"Observations {observation_staleness_hours:.1f}h stale "
                        f"(threshold: {obs_partial_hours:.1f}h)"
                    ),
                )
            )

    # NWP cycle age. Runoff-only mode has no NWP at all: emit a distinct
    # human-readable flag regardless of nwp_age_hours (which is meaningless
    # without a cycle) and short-circuit the age-based branches below.
    if nwp_cycle_source == NwpCycleSource.RUNOFF_ONLY:
        flags.append(
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.DEGRADED,
                detail="No NWP forcing: runoff-only mode (weather forecast disabled)",
            )
        )
    elif nwp_age_hours >= config.nwp_age_degraded_hours:
        fallback_note = (
            ", fallback cycle" if nwp_cycle_source == NwpCycleSource.FALLBACK else ""
        )
        flags.append(
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.DEGRADED,
                detail=(
                    f"NWP {nwp_age_hours:.1f}h stale{fallback_note} "
                    f"(threshold: {config.nwp_age_degraded_hours:.1f}h)"
                ),
            )
        )
    elif nwp_age_hours >= config.nwp_age_partial_hours:
        fallback_note = (
            ", fallback cycle" if nwp_cycle_source == NwpCycleSource.FALLBACK else ""
        )
        flags.append(
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.PARTIAL,
                detail=(
                    f"NWP {nwp_age_hours:.1f}h stale{fallback_note} "
                    f"(threshold: {config.nwp_age_partial_hours:.1f}h)"
                ),
            )
        )

    # Warm-up state
    if warm_up_source is not None and warm_up_source != WarmUpSource.FRESH:
        if warm_up_source == WarmUpSource.COLD_START:
            flags.append(
                InputQualityFlag(
                    category=InputQualityCategory.WARM_UP,
                    level=InputQualityLevel.DEGRADED,
                    detail="Cold start (no warm-up snapshot available)",
                )
            )
        elif warm_up_source == WarmUpSource.SNAPSHOT:
            if warm_up_state_age_hours is None:
                flags.append(
                    InputQualityFlag(
                        category=InputQualityCategory.WARM_UP,
                        level=InputQualityLevel.DEGRADED,
                        detail="Warm-up snapshot age unknown",
                    )
                )
            elif warm_up_state_age_hours >= warmup_degraded_hours:
                flags.append(
                    InputQualityFlag(
                        category=InputQualityCategory.WARM_UP,
                        level=InputQualityLevel.DEGRADED,
                        detail=(
                            f"Warm-up snapshot {warm_up_state_age_hours:.1f}h old "
                            f"(threshold: {warmup_degraded_hours:.1f}h)"
                        ),
                    )
                )
            elif warm_up_state_age_hours >= warmup_partial_hours:
                flags.append(
                    InputQualityFlag(
                        category=InputQualityCategory.WARM_UP,
                        level=InputQualityLevel.PARTIAL,
                        detail=(
                            f"Warm-up snapshot {warm_up_state_age_hours:.1f}h old "
                            f"(threshold: {warmup_partial_hours:.1f}h)"
                        ),
                    )
                )

    level = aggregate_input_quality(flags)
    return level, tuple(flags)


def assess_past_forcing_gaps(
    *,
    series: str,
    expected: Sequence[UtcDatetime],
    missing: Sequence[UtcDatetime],
    anchor: UtcDatetime,
    time_step: timedelta,
    recent_steps: int,
) -> InputQualityFlag | None:
    """Label gaps in one past-forcing series. NEVER refuses.

    Plan 239 T1b, owner decision 2026-09-08: **where** the gap sits decides
    severity, not how many there are. Two missing days out of 210 long ago is
    not a problem; the same two at the END is, because the model leans on
    recent conditions. Either way the forecast is still produced — a refusal
    throws away information the forecast still carries.

    `recent_steps` is a FIXED count of most-recent buckets, one rule for every
    model. Scaling it with `lookback` was considered and rejected as harder to
    explain for no gain, and a per-model declaration would need an FI contract
    change first (no model declares this today).

    Returns ``None`` when nothing is missing. Judging VALUES is not this
    function's job — that is `max_nan`'s, per the T0 specification.
    """
    if not missing:
        return None

    base = floor_to_time_step(anchor, time_step)
    # The recent window is the `recent_steps` complete buckets before `base`:
    # base - 1*step ... base - recent_steps*step. `base` itself is the bucket
    # in progress and is never in the expected past set (T0).
    cutoff = ensure_utc(base - recent_steps * time_step)
    recent = sorted(slot for slot in missing if slot >= cutoff)

    if recent:
        shown = ", ".join(str(slot) for slot in recent[:3])
        more = f" (+{len(recent) - 3} more)" if len(recent) > 3 else ""
        return InputQualityFlag(
            category=InputQualityCategory.FORCING,
            level=InputQualityLevel.DEGRADED,
            detail=(
                f"'{series}' missing {len(recent)} of the {recent_steps} most "
                f"recent steps: {shown}{more} "
                f"({len(missing)} of {len(expected)} missing overall)"
            ),
        )

    return InputQualityFlag(
        category=InputQualityCategory.FORCING,
        level=InputQualityLevel.PARTIAL,
        detail=(
            f"'{series}' missing {len(missing)} of {len(expected)} past steps, "
            f"none within the {recent_steps} most recent"
        ),
    )


def past_forcing_flags(
    *,
    past_dynamic: pl.DataFrame,
    features: Iterable[str],
    anchor: UtcDatetime,
    time_step: timedelta,
    lookback_steps: int,
    recent_steps: int,
    declared_lookbacks: Mapping[str, int] | None = None,
) -> list[InputQualityFlag]:
    """One flag per declared past-forcing series that has gaps (Plan 239 T1b).

    Per-SERIES, not per-frame: the T0 specification is explicit that a model
    declaring unequal lookbacks (7-day target, 45-day precipitation, 14-day
    temperature) must be judged on each series' own window, never on a
    collapsed maximum.

    `lookback_steps` IS that collapsed maximum — input assembly fetches one
    frame wide enough for every declared variable. An earlier revision judged
    every series against it, which reported a 30-day-old temperature hole for a
    model that only reads 14 days of temperature (independent review,
    2026-09-09). `declared_lookbacks` carries each variable's own declaration
    from `ModelDataRequirements`; a series absent from it falls back to the
    maximum, which is the correct conservative default for a model that
    declares one window for everything.
    """
    if lookback_steps <= 0:
        return []
    declared = declared_lookbacks or {}
    # One `expected` set per DISTINCT window, not per series — a model with 20
    # features on one window should not rebuild the same bucket list 20 times.
    expected_by_window: dict[int, Sequence[UtcDatetime]] = {}
    flags: list[InputQualityFlag] = []
    for series in sorted(features):
        window = min(declared.get(series, lookback_steps), lookback_steps)
        if window <= 0:
            continue
        if window not in expected_by_window:
            expected_by_window[window] = expected_past_buckets(
                anchor, time_step, window
            )
        expected = expected_by_window[window]
        flag = assess_past_forcing_gaps(
            series=series,
            expected=expected,
            missing=missing_buckets(past_dynamic, series, expected),
            anchor=anchor,
            time_step=time_step,
            recent_steps=recent_steps,
        )
        if flag is not None:
            flags.append(flag)
    return flags
