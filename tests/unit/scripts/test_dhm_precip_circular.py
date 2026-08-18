"""Plan 182 (M-A10) — circular hour-of-day arithmetic.

D9: "toward" is a REDUCTION IN CIRCULAR DISTANCE, never a signed direction
(undefined for antipodal peaks). D5: bootstrap spread must be CIRCULAR — peaks
split 23:00/00:00 are 1h apart, not 23.

Imports are guarded so a not-yet-implemented module fails these tests as a
genuine RED assertion, never a collection-time ImportError.
"""

from __future__ import annotations

try:
    from scripts.dhm_precip.circular import (
        circ_dist_hours,
        circular_range_hours,
        moves_toward,
    )
except ImportError:
    circ_dist_hours = None  # type: ignore[assignment]
    circular_range_hours = None  # type: ignore[assignment]
    moves_toward = None  # type: ignore[assignment]


class TestCircDistHours:
    def test_zero_distance_for_identical_hours(self) -> None:
        assert circ_dist_hours is not None, "circ_dist_hours not implemented yet"
        assert circ_dist_hours(14.0, 14.0) == 0.0

    def test_wraps_across_midnight(self) -> None:
        assert circ_dist_hours is not None, "circ_dist_hours not implemented yet"
        # 23:00 -> 01:00 is 2h the short way round, not 22h.
        assert circ_dist_hours(23.0, 1.0) == 2.0

    def test_maximum_distance_is_half_the_period(self) -> None:
        assert circ_dist_hours is not None, "circ_dist_hours not implemented yet"
        assert circ_dist_hours(2.0, 14.0) == 12.0

    def test_symmetric(self) -> None:
        assert circ_dist_hours is not None, "circ_dist_hours not implemented yet"
        assert circ_dist_hours(3.0, 21.0) == circ_dist_hours(21.0, 3.0)


class TestMovesToward:
    def test_true_when_circular_distance_shrinks(self) -> None:
        assert moves_toward is not None, "moves_toward not implemented yet"
        # target=12; before=10 (dist 2); after=6 (dist 6) -> moved AWAY.
        # before=6 (dist 6); after=10 (dist 2) -> moved TOWARD.
        assert moves_toward(before=6.0, after=10.0, target=12.0) is True

    def test_false_when_circular_distance_grows(self) -> None:
        assert moves_toward is not None, "moves_toward not implemented yet"
        assert moves_toward(before=10.0, after=6.0, target=12.0) is False

    def test_antipodal_target_has_no_undefined_direction(self) -> None:
        """D9: 'DHM at 08:00, Pyramid at 20:00 — antipodal — "moves 4h toward"
        has no unique direction on a circle.' The circular-distance test
        sidesteps this: AT the antipode (distance = period/2, the maximum),
        moving 4h either rotational way strictly reduces the distance, so
        both directions register as 'toward' — there is no ambiguity to
        resolve because no signed direction is ever consulted."""
        assert moves_toward is not None, "moves_toward not implemented yet"
        assert moves_toward(before=8.0, after=4.0, target=20.0) is True
        assert moves_toward(before=8.0, after=12.0, target=20.0) is True


class TestCircularRangeHours:
    def test_single_value_has_zero_spread(self) -> None:
        assert circular_range_hours is not None, (
            "circular_range_hours not implemented yet"
        )
        assert circular_range_hours([14.0]) == 0.0

    def test_identical_values_have_zero_spread(self) -> None:
        assert circular_range_hours is not None, (
            "circular_range_hours not implemented yet"
        )
        assert circular_range_hours([23.0, 23.0, 23.0]) == 0.0

    def test_midnight_split_is_one_hour_not_twenty_three(self) -> None:
        """D5: peaks split 23:00/00:00 are 1h apart, a NAIVE (non-circular)
        max-minus-min would report 23h."""
        assert circular_range_hours is not None, (
            "circular_range_hours not implemented yet"
        )
        assert circular_range_hours([23.0, 0.0, 0.0]) == 1.0

    def test_evenly_spread_values_have_wide_range(self) -> None:
        assert circular_range_hours is not None, (
            "circular_range_hours not implemented yet"
        )
        # 0, 8, 16 are maximally spread on a 24h circle (each gap = 8h);
        # the smallest enclosing arc must span the two occupied gaps: 16h.
        assert circular_range_hours([0.0, 8.0, 16.0]) == 16.0
