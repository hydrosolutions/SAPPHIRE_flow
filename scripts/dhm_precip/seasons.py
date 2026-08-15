"""D8 (Plan 173, M-A3) — the conventional Nepali four-season split. The
parameter object names only JJAS and DJF; MAM and ON fill the remaining
eight months so every axis row lands in exactly one bin
(`DhmPrecipParams.__post_init__` enforces the partition is exhaustive and
disjoint at construction time).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from scripts.dhm_precip.params import DhmPrecipParams


class Season(StrEnum):
    MAM = "MAM"
    JJAS = "JJAS"
    ON = "ON"
    DJF = "DJF"


class UnclassifiedMonthError(ValueError):
    """Raised only if a caller supplies a `DhmPrecipParams` whose season
    tuples do not partition 1..12 — `__post_init__` already forbids
    constructing such an object, so this is a defence-in-depth guard, not a
    reachable path under a validly-constructed params object."""


def season_for(timestamp: UtcDatetime, params: DhmPrecipParams) -> Season:
    month = timestamp.month
    if month in params.jjas_months:
        return Season.JJAS
    if month in params.djf_months:
        return Season.DJF
    if month in params.mam_months:
        return Season.MAM
    if month in params.on_months:
        return Season.ON
    raise UnclassifiedMonthError(
        f"month {month} is not covered by any of params' season tuples"
    )
