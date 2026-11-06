from datetime import UTC, datetime, timedelta
from fractions import Fraction
from uuid import UUID

import pytest

from sapphire_flow.services.observation_history import summarize_history
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.observation_history import (
    HistoryGrid,
    HistoryReading,
    HistoryWindow,
)

SID = StationId(UUID(int=1))
A = ensure_utc(datetime(2026, 11, 5, 10, 20, 0, 123456, tzinfo=UTC))


def reading(timestamp: datetime, value: float) -> HistoryReading:
    return HistoryReading(station_id=SID, timestamp=timestamp, value=value)


class TestSummarizeHistory:
    @pytest.mark.parametrize(
        "values",
        [
            [0.1] * 3,
            [12.3] * 18,
            [1e308, 1e308],
            [1e308, 1.0, -1e308],
            [5e-324, 5e-324, 0.0],
        ],
    )
    def test_exact_mean_rounded_once(self, values: list[float]) -> None:
        grid = HistoryGrid(as_of=A, window=HistoryWindow.THREE_DAYS)
        rows = [
            reading(ensure_utc(grid.start + timedelta(seconds=i)), value)
            for i, value in enumerate(values)
        ]
        result = summarize_history(grid, (SID,), rows)[0]
        block = result.coarse[0]
        assert block.mean == float(sum(map(Fraction, values)) / len(values))
        assert (block.minimum, block.maximum, block.count) == (
            min(values),
            max(values),
            len(values),
        )

    def test_exact_microsecond_ownership(self) -> None:
        grid = HistoryGrid(as_of=A, window=HistoryWindow.THREE_DAYS)
        times = [
            grid.start - timedelta(microseconds=1),
            grid.start,
            grid.native_start - timedelta(microseconds=1),
            grid.native_start,
            A,
            A + timedelta(microseconds=1),
        ]
        result = summarize_history(
            grid,
            (SID,),
            [reading(ensure_utc(t), float(i)) for i, t in enumerate(times)],
        )[0]
        assert sum(b.count for b in result.coarse) == 2
        assert [(r.timestamp, r.value) for r in result.native] == [
            (grid.native_start, 3.0),
            (A, 4.0),
        ]
        assert grid.grid_start == datetime(2026, 11, 2, 9, tzinfo=UTC)
        assert grid.native_start == datetime(2026, 11, 5, 9, tzinfo=UTC)

    def test_duplicate_identical_once_conflicting_refused(self) -> None:
        grid = HistoryGrid(as_of=A, window=HistoryWindow.SEVEN_DAYS)
        row = reading(grid.start, 0.1)
        assert summarize_history(grid, (SID,), [row, row])[0].coarse[0].count == 1
        with pytest.raises(ValueError, match="conflicting"):
            summarize_history(grid, (SID,), [row, reading(grid.start, 0.2)])

    def test_nonfinite_inert_and_empty_stations_preserved(self) -> None:
        grid = HistoryGrid(as_of=A, window=HistoryWindow.SEVEN_DAYS)
        result = summarize_history(
            grid, (SID,), [reading(grid.start, float("nan")), reading(A, float("inf"))]
        )[0]
        assert all(b.count == 0 and b.mean is None for b in result.coarse)
        assert result.native == ()

    def test_signed_zero_native_and_extrema_preserved(self) -> None:
        grid = HistoryGrid(as_of=A, window=HistoryWindow.SEVEN_DAYS)
        result = summarize_history(
            grid, (SID,), [reading(grid.start, -0.0), reading(A, -0.0)]
        )[0]
        assert result.coarse[0].minimum.hex() == (-0.0).hex()
        assert result.native[0].value.hex() == (-0.0).hex()


class TestHistoryGrid:
    @pytest.mark.parametrize(
        "cutoff",
        [
            datetime(2024, 2, 29, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 9, tzinfo=UTC),
            datetime(2026, 3, 29, 1, tzinfo=UTC),
            datetime(2026, 10, 25, 1, tzinfo=UTC),
        ],
    )
    @pytest.mark.parametrize("window", list(HistoryWindow))
    def test_calendar_edges_exact(
        self, cutoff: datetime, window: HistoryWindow
    ) -> None:
        grid = HistoryGrid(as_of=ensure_utc(cutoff), window=window)
        assert grid.start == cutoff - timedelta(hours=window)
        assert timedelta(hours=1) <= cutoff - grid.native_start < timedelta(hours=4)
        assert (
            grid.grid_start + grid.block_count * timedelta(hours=3) == grid.native_start
        )
