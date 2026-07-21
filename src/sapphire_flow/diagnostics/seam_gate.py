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

import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sapphire_flow.types.forcing_sources import ForcingSource
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
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


class SeamEdge(Enum):
    """Which side of a seam a window of raw rows sits on.

    Relative to an explicit ``seam_time``, ``BEFORE`` selects the LAST
    ``window_size`` rows with ``valid_time < seam_time`` (nearest the seam);
    ``AFTER`` selects the FIRST ``window_size`` rows with
    ``valid_time >= seam_time``. Without seam-relative selection, a builder
    that merely filters by source/parameter compares arbitrary history lengths
    instead of "around the seam", and — with deferred RprelimD supersession —
    an AFTER window could even be drawn from pre-seam rows (Plan 129 BUG 2).
    """

    BEFORE = auto()
    AFTER = auto()


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
        if not math.isfinite(value) or value < 0.0 or value > _PLAUSIBLE_MM_DAY_MAX
    ]
    if out_of_range:
        return SeamGateResult(
            verdict=SeamGateVerdict.UNIT_ERROR_SUSPECTED,
            detail=(
                f"{len(out_of_range)} value(s) outside the plausible "
                f"[0, {_PLAUSIBLE_MM_DAY_MAX}] mm/day range (or non-finite): "
                f"{out_of_range[:3]}"
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


def _select_seam_local(
    ordered: Sequence[tuple[datetime, float]],
    *,
    edge: SeamEdge,
    window_size: int,
    seam_time: datetime,
) -> tuple[float, ...]:
    """From ``(valid_time, value)`` pairs sorted by ``valid_time``, select the
    ``window_size`` rows immediately BEFORE (``valid_time < seam_time``,
    nearest the seam) or AFTER (``valid_time >= seam_time``, nearest) it.

    **Anchoring on an explicit ``seam_time`` (Plan 129 BUG 2 fix)** — not on
    the fetched series' own endpoints — is what localizes the window to the
    real handoff. Because RprelimD supersession is DEFERRED, raw RprelimD rows
    can carry ``valid_time``s that overlap dates already covered by RhiresD;
    taking a bare ``ordered[:window_size]`` for AFTER would then pull the
    window from BEFORE/INSIDE the RhiresD period and silently check the wrong
    handoff. Filtering to the correct side of ``seam_time`` first, then taking
    the nearest ``window_size`` rows, excludes those overlapping pre-seam rows.
    """
    if edge is SeamEdge.BEFORE:
        selected = [pair for pair in ordered if pair[0] < seam_time][-window_size:]
    else:
        selected = [pair for pair in ordered if pair[0] >= seam_time][:window_size]
    return tuple(value for _valid_time, value in selected)


def seam_window_from_forcing_rows(
    rows: Sequence[RawHistoricalForcing],
    *,
    station_id: StationId,
    source: ForcingSource,
    parameter: str,
    label: str,
    edge: SeamEdge,
    window_size: int,
    seam_time: datetime,
) -> SeamWindow:
    """Build a ``SeamWindow`` from the ``window_size`` raw reanalysis rows
    immediately before/after ``seam_time``, tagged with ``source``.

    Reads ``.source`` (dropped by the pivoted ``past_dynamic``/``future_dynamic``
    frames) — this is why the gate must run over raw rows, not assembled ones.
    Selects by ``valid_time`` proximity to the explicit ``seam_time``
    (``edge``), not merely every matching row nor the fetched series' own
    endpoints — an unbounded raw fetch, OR a deferred-supersession RprelimD
    fetch whose rows overlap the RhiresD period, would otherwise pull in rows
    far from (or on the wrong side of) the seam and change the verdict (Plan
    129 BUG 2). Filters by ``station_id`` — a multi-station raw fetch (e.g. a
    T1 query over both staging stations) would otherwise interleave rows from
    different basins and silently check the wrong seam.
    """
    matched: list[tuple[datetime, float]] = sorted(
        (
            (row.valid_time, row.value)
            for row in rows
            if row.station_id == station_id
            and row.source == source.value
            and row.parameter == parameter
        ),
        key=lambda pair: pair[0],
    )
    return SeamWindow(
        label=label,
        values=_select_seam_local(
            matched, edge=edge, window_size=window_size, seam_time=seam_time
        ),
    )


def seam_window_from_nwp_rows(
    rows: Sequence[WeatherForecastRecord],
    *,
    station_id: StationId,
    nwp_source: str,
    cycle_time: datetime,
    parameter: str,
    label: str,
    edge: SeamEdge,
    window_size: int,
    seam_time: datetime,
) -> SeamWindow:
    """Build a ``SeamWindow`` from the ``window_size`` raw NWP rows immediately
    before/after ``seam_time``, for one parameter.

    Collapses every ensemble member to a single mean value per ``valid_time``
    BEFORE seam-local selection: a raw 21-member ensemble fetch has ~21x the
    magnitude of one deterministic reanalysis row at the same valid times, so
    summing member values into the window's scale statistic
    (``_representative_magnitude``) would inflate the NWP side and falsely
    flag a correctly-scaled seam. Filters by ``station_id``, ``nwp_source``,
    and ``cycle_time`` — a raw multi-station or multi-cycle fetch would
    otherwise mix rows from a different basin or an earlier/later model run
    into the same window and silently check the wrong seam.
    """
    by_valid_time: dict[datetime, list[float]] = {}
    for row in rows:
        if (
            row.station_id != station_id
            or row.nwp_source != nwp_source
            or row.cycle_time != cycle_time
            or row.parameter != parameter
        ):
            continue
        by_valid_time.setdefault(row.valid_time, []).append(row.value)

    collapsed: list[tuple[datetime, float]] = sorted(
        (
            (valid_time, sum(values) / len(values))
            for valid_time, values in by_valid_time.items()
        ),
        key=lambda pair: pair[0],
    )
    return SeamWindow(
        label=label,
        values=_select_seam_local(
            collapsed, edge=edge, window_size=window_size, seam_time=seam_time
        ),
    )
