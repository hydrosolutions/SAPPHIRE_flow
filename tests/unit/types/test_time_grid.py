from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sapphire_flow.services.training_data import (
    aligned_lookback_bounds,
    expected_future_buckets,
    floor_to_time_step,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.time_grid import TimeGrid, require_common_time_grid


class TestTimeGrid:
    @pytest.mark.parametrize(
        ("step", "phase", "message"),
        [
            (timedelta(0), timedelta(0), "positive"),
            (timedelta(hours=-1), timedelta(0), "positive"),
            (timedelta(hours=7), timedelta(0), "divide 24"),
            (timedelta(days=2), timedelta(0), "divide 24"),
            (timedelta(days=1), timedelta(minutes=-1), "0 <= phase < step"),
            (timedelta(days=1), timedelta(days=1), "0 <= phase < step"),
            (timedelta(days=1), timedelta(seconds=1), "whole number of minutes"),
        ],
    )
    def test_rejects_invalid_grid(
        self, step: timedelta, phase: timedelta, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            TimeGrid(step=step, phase=phase)

    def test_nesting_requires_matching_boundaries(self) -> None:
        utc = TimeGrid(step=timedelta(hours=1), phase=timedelta(0))
        npt = TimeGrid(step=timedelta(hours=1), phase=timedelta(minutes=15))
        ten = TimeGrid(step=timedelta(minutes=10), phase=timedelta(0))
        quarter = TimeGrid(step=timedelta(minutes=15), phase=timedelta(0))
        assert not utc.alignable(npt)
        assert ten.nests_into(utc)
        assert not ten.nests_into(npt)
        assert quarter.nests_into(utc) and quarter.nests_into(npt)
        assert not utc.nests_into(quarter)

    @pytest.mark.parametrize(
        ("anchor", "boundary"),
        [
            ("2026-11-17T17:59:59.999999+00:00", "2026-11-16T18:00:00+00:00"),
            ("2026-11-17T18:00:00+00:00", "2026-11-17T18:00:00+00:00"),
            ("2026-11-17T18:00:00.000001+00:00", "2026-11-17T18:00:00+00:00"),
            ("2026-01-01T00:00:00+00:00", "2025-12-31T18:00:00+00:00"),
            ("2024-03-01T00:00:00+00:00", "2024-02-29T18:00:00+00:00"),
            ("1969-12-31T17:59:59.999999+00:00", "1969-12-30T18:00:00+00:00"),
        ],
    )
    def test_nepal_floor(self, anchor: str, boundary: str) -> None:
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
        assert grid.floor(
            ensure_utc(datetime.fromisoformat(anchor))
        ) == datetime.fromisoformat(boundary)

    @pytest.mark.parametrize("hours", [1, 3, 6, 24])
    @pytest.mark.parametrize("minute", [0, 1])
    def test_phase_zero_matches_existing_bounds(self, hours: int, minute: int) -> None:
        step = timedelta(hours=hours)
        grid = TimeGrid(step=step, phase=timedelta(0))
        anchor = ensure_utc(datetime(2026, 11, 17, minute=minute, tzinfo=UTC))
        assert grid.floor(anchor) == floor_to_time_step(anchor, step)
        assert grid.lookback_bounds(anchor, 30) == aligned_lookback_bounds(
            anchor, 30, step
        )
        starts = expected_future_buckets(anchor, step, 10)
        assert grid.future_bounds(anchor, 10) == (starts[0], starts[-1] + step)

    @pytest.mark.parametrize("microseconds", [0, 1])
    def test_complete_periods_exclude_partial_buckets(self, microseconds: int) -> None:
        day = timedelta(days=1)
        grid = TimeGrid(step=day, phase=timedelta(hours=18))
        boundary = ensure_utc(datetime(2026, 11, 17, 18, tzinfo=UTC))
        anchor = ensure_utc(boundary + timedelta(microseconds=microseconds))
        future_start = boundary + (day if microseconds else timedelta(0))
        assert grid.lookback_bounds(anchor, 30) == (boundary - 30 * day, boundary)
        assert grid.future_bounds(anchor, 10) == (future_start, future_start + 10 * day)
        assert grid.lookback_bounds(anchor, 0) == (boundary, boundary)
        assert grid.future_bounds(anchor, 0) == (future_start, future_start)

    def test_accepts_minute_phase_without_deriving_it_from_display_zone(self) -> None:
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18, minutes=15))
        local_midnight = datetime(
            2026, 11, 18, tzinfo=timezone(timedelta(hours=5, minutes=45))
        )
        assert grid.floor(ensure_utc(local_midnight)) == datetime(
            2026, 11, 17, 18, 15, tzinfo=UTC
        )

    def test_naive_instant_is_rejected(self) -> None:
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(0))
        with pytest.raises(ValueError, match="Naive datetime"):
            grid.floor(datetime(2026, 11, 17))  # type: ignore[arg-type]

    def test_negative_count_is_rejected(self) -> None:
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(0))
        anchor = ensure_utc(datetime(2026, 11, 17, tzinfo=UTC))
        for bounds in (grid.lookback_bounds, grid.future_bounds):
            with pytest.raises(ValueError, match="nonnegative integer"):
                bounds(anchor, -1)


class TestRequireCommonTimeGrid:
    @pytest.mark.parametrize("different_step", [False, True])
    def test_rejects_incompatible_group(self, different_step: bool) -> None:
        first = TimeGrid(step=timedelta(days=1), phase=timedelta(0))
        other = TimeGrid(
            step=timedelta(hours=6) if different_step else timedelta(days=1),
            phase=timedelta(0) if different_step else timedelta(hours=18),
        )
        with pytest.raises(ValueError, match="same time-grid step and phase"):
            require_common_time_grid([first, other])

    def test_empty_group_has_no_implicit_grid(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            require_common_time_grid([])

    def test_equal_grids_are_accepted(self) -> None:
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
        assert require_common_time_grid([grid, grid]) == grid
