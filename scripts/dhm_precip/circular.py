"""Plan 182 (M-A10) D9 — circular hour-of-day arithmetic.

Hour-of-day is a POINT ON A 24h CIRCLE, never a linear scalar: 23:00 and
01:00 are 2h apart, not 22h. Every phase comparison in the co-located gauge
adjudication (peak-hour movement, "toward", bootstrap spread) goes through
this module — nowhere else computes a naive `abs(a - b)` on an hour value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_PERIOD_HOURS = 24.0


def circ_dist_hours(a: float, b: float, *, period: float = _PERIOD_HOURS) -> float:
    """The shorter arc between two hour-of-day points, in `[0, period/2]`."""
    diff = abs(a - b) % period
    return min(diff, period - diff)


def moves_toward(
    *, before: float, after: float, target: float, period: float = _PERIOD_HOURS
) -> bool:
    """D9 — 'toward' is defined as a REDUCTION IN CIRCULAR DISTANCE to
    `target`, never as a signed direction (undefined for antipodal peaks:
    at the exact antipode, moving either rotational way reduces the
    distance equally, so both directions correctly register as 'toward'
    without ever needing to resolve which one is "the" direction)."""
    return circ_dist_hours(after, target, period=period) < circ_dist_hours(
        before, target, period=period
    )


def circular_range_hours(
    values: Sequence[float], *, period: float = _PERIOD_HOURS
) -> float:
    """D5 — the smallest arc containing every value (the 'circular range').
    A naive linear `max - min` would report 23h for `[23, 0]`; this reports
    1h, because 23:00 and 00:00 are neighbours on the clock, not opposite
    ends of a line."""
    if not values:
        raise ValueError("circular_range_hours requires at least one value")
    ordered = sorted(v % period for v in values)
    if len(ordered) == 1:
        return 0.0
    gaps = [b - a for a, b in zip(ordered, ordered[1:], strict=False)]
    gaps.append(period - ordered[-1] + ordered[0])
    return period - max(gaps)
