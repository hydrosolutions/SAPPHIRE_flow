from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.domain import InputQualityFlag, aggregate_input_quality

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import InputQualityConfig
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
