"""S1 — the continuous-precipitation seam-continuity gate (Plan 129).

A coarse units/scale sanity check invoked ONLY by the T1 staging check, over
the RAW provenance-bearing rows (``RawHistoricalForcing`` — which carries
``.source`` — and raw NWP ``WeatherForecastRecord`` rows), at the two seams
that knit a continuous precipitation series:

* **RhiresD -> RprelimD** (within the reanalysis rows) — both are MeteoSwiss
  daily precip in mm/day; the gate guards against a unit/scale regression at
  the product handoff, not value agreement.
* **RprelimD -> NWP** (reanalysis rows vs NWP rows) — the last RprelimD
  day(s) and first NWP day(s) must be in the same mm/day scale (catch an ICON
  precip unit/accumulation error), tolerating a real meteorological
  difference (a forecast rain event RprelimD lacks).

This is a T1-ONLY DIAGNOSTIC (owner decision 2, Plan 129) — **not** wired
into operational assembly, and not a per-day value-agreement or statistical
test. It runs on the raw rows because ``_raw_forcing_to_dataframe`` keeps
only timestamp + values and drops ``source`` (``operational_inputs.py``,
``training_data.py``), so the seams are not locatable in the assembled frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.forcing_sources import ForcingSource
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.weather import WeatherForecastRecord

# Generous Alpine daily-precip sanity ceiling (mm/day). A value outside
# [0, this] at either side of a seam is implausible on its own — independent
# of any cross-seam ratio — and is flagged directly.
_PLAUSIBLE_MM_DAY_MAX = 300.0

# Below the smallest named unit-bug scale (per-hour-vs-per-day, ~24x) and
# comfortably above ordinary day-to-day meteorological variability summed
# over a multi-day window — a coarse, high-tolerance separator (owner
# decision 2: never flag a legitimate met difference).
_MAGNITUDE_RATIO_FLAG_THRESHOLD = 15.0


class SeamGateVerdict(Enum):
    PASS = auto()
    UNIT_ERROR_SUSPECTED = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class SeamWindow:
    """A window of raw precip values (mm/day-equivalent) on one side of a seam."""

    label: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError(f"seam window {self.label!r} has no values")


@dataclass(frozen=True, kw_only=True, slots=True)
class SeamGateResult:
    verdict: SeamGateVerdict
    detail: str


def _representative_magnitude(values: tuple[float, ...]) -> float:
    """Sum of absolute values — a window-level scale statistic robust to
    zero-inflated daily precip (a single dry/wet day must not dominate)."""
    return sum(abs(v) for v in values)


def check_seam_continuity(before: SeamWindow, after: SeamWindow) -> SeamGateResult:
    """Coarse units/scale sanity check across one seam.

    Flags an implausible absolute value or an order-of-magnitude scale
    mismatch between the two windows; tolerates a real meteorological
    difference (e.g. a forecast rain event the past window lacks).
    """
    out_of_range = [
        (window.label, value)
        for window in (before, after)
        for value in window.values
        if value < 0.0 or value > _PLAUSIBLE_MM_DAY_MAX
    ]
    if out_of_range:
        return SeamGateResult(
            verdict=SeamGateVerdict.UNIT_ERROR_SUSPECTED,
            detail=(
                f"{len(out_of_range)} value(s) outside the plausible "
                f"[0, {_PLAUSIBLE_MM_DAY_MAX}] mm/day range: {out_of_range[:3]}"
            ),
        )

    before_magnitude = _representative_magnitude(before.values)
    after_magnitude = _representative_magnitude(after.values)
    if before_magnitude == 0.0 or after_magnitude == 0.0:
        return SeamGateResult(
            verdict=SeamGateVerdict.PASS,
            detail="one window is all-zero precip; no scale ratio to test",
        )

    larger, smaller = (
        max(before_magnitude, after_magnitude),
        min(before_magnitude, after_magnitude),
    )
    ratio = larger / smaller
    if ratio >= _MAGNITUDE_RATIO_FLAG_THRESHOLD:
        return SeamGateResult(
            verdict=SeamGateVerdict.UNIT_ERROR_SUSPECTED,
            detail=(
                f"{before.label}={before_magnitude:.3f} vs "
                f"{after.label}={after_magnitude:.3f} mm/day-equivalent, "
                f"ratio={ratio:.1f}x >= {_MAGNITUDE_RATIO_FLAG_THRESHOLD:.0f}x"
            ),
        )

    return SeamGateResult(
        verdict=SeamGateVerdict.PASS,
        detail=f"ratio={ratio:.1f}x within tolerance",
    )


def seam_window_from_forcing_rows(
    rows: Sequence[RawHistoricalForcing],
    *,
    source: ForcingSource,
    parameter: str,
    label: str,
) -> SeamWindow:
    """Build a ``SeamWindow`` from raw reanalysis rows tagged with ``source``.

    Reads ``.source`` (dropped by the pivoted ``past_dynamic``/``future_dynamic``
    frames) — this is why the gate must run over raw rows, not assembled ones.
    """
    values = tuple(
        row.value
        for row in rows
        if row.source == source.value and row.parameter == parameter
    )
    return SeamWindow(label=label, values=values)


def seam_window_from_nwp_rows(
    rows: Sequence[WeatherForecastRecord],
    *,
    parameter: str,
    label: str,
) -> SeamWindow:
    """Build a ``SeamWindow`` from raw NWP rows for one parameter.

    Coarse by design: does not filter by ensemble member — every member's
    value at the seam is the same order of magnitude, which is all a
    units/scale sanity check needs.
    """
    values = tuple(row.value for row in rows if row.parameter == parameter)
    return SeamWindow(label=label, values=values)
