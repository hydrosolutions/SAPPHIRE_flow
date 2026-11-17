import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from sapphire_flow.preprocessing.recap_operational_weather import (
    reduce_daily_window,
    reduce_weather_periods,
)
from sapphire_flow.store.operational_weather_codec import window_bytes
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    OperationalWeatherCoverageError,
    OperationalWeatherSample,
    OperationalWeatherSource,
    WeatherCoverage,
    WeatherProductIdentity,
)
from sapphire_flow.types.time_grid import TimeGrid

DAY = ensure_utc(datetime(2026, 11, 14, tzinfo=UTC))
GRID = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
START = ensure_utc(DAY + GRID.phase)
END = ensure_utc(START + timedelta(days=2))


def hourly_samples(
    start: UtcDatetime, hours: range
) -> tuple[OperationalWeatherSample, ...]:
    return tuple(
        OperationalWeatherSample(
            parameter=parameter,
            source=OperationalWeatherSource.ERA5_LAND,
            valid_time=ensure_utc(start + timedelta(hours=hour)),
            source_run=ensure_utc((start + timedelta(hours=hour)).replace(hour=0)),
            value=float(hour + 1),
            product=WeatherProductIdentity.REANALYSIS,
            interval_end=ensure_utc(start + timedelta(hours=hour + 1))
            if parameter == "precipitation"
            else None,
        )
        for parameter in ("precipitation", "temperature")
        for hour in hours
    )


class TestReduceWeatherPeriods:
    def test_two_periods_preserve_values_source_dates_and_sample_order(self) -> None:
        samples = tuple(reversed(hourly_samples(START, range(-1, 49))))
        periods = reduce_weather_periods(
            start=START, end=END, samples=samples, grid=GRID
        )
        assert [(p.start, p.end) for p in periods] == [
            (START, START + timedelta(days=1)),
            (START + timedelta(days=1), END),
        ]
        assert [p.precipitation_mm for p in periods] == [300.0, 876.0]
        assert [p.temperature_c for p in periods] == [12.5, 36.5]
        consumed = tuple(s for p in periods for s in p.samples)
        assert len(consumed) == 96
        assert len({id(s) for s in consumed}) == 96
        assert {s.source_run for s in consumed} == {
            DAY,
            DAY + timedelta(days=1),
            DAY + timedelta(days=2),
        }
        for period in periods:
            expected = tuple(
                s for s in samples if period.start <= s.valid_time < period.end
            )
            assert all(a is b for a, b in zip(period.samples, expected, strict=True))
        assert any(
            s.valid_time == START + timedelta(hours=23) for s in periods[0].samples
        )
        assert any(
            s.valid_time == START + timedelta(hours=24) for s in periods[1].samples
        )
        assert all(START <= s.valid_time < END for s in consumed)

    def test_missing_and_empty_periods_are_retained_when_allowed(self) -> None:
        samples = tuple(
            s
            for s in hourly_samples(START, range(24))
            if not (s.parameter == "precipitation" and s.valid_time == START)
        )
        periods = reduce_weather_periods(
            start=START,
            end=END,
            samples=samples,
            grid=GRID,
            coverage=WeatherCoverage.ALLOW_GAPS,
        )
        assert [(p.precipitation_mm, p.temperature_c) for p in periods] == [
            (None, 12.5),
            (None, None),
        ]
        assert periods[1].samples == ()

    def test_complete_mode_refuses_missing_support(self) -> None:
        with pytest.raises(OperationalWeatherCoverageError, match="gap"):
            reduce_weather_periods(start=START, end=END, samples=(), grid=GRID)

    def test_duplicate_sample_refused_even_when_gaps_allowed(self) -> None:
        samples = hourly_samples(START, range(24))
        with pytest.raises(ValueError, match="overlap or duplicate"):
            reduce_weather_periods(
                start=START,
                end=END,
                samples=(*samples, samples[0]),
                grid=GRID,
                coverage=WeatherCoverage.ALLOW_GAPS,
            )

    def test_cut_through_native_interval_is_not_split(self) -> None:
        shift = timedelta(minutes=15)
        grid = TimeGrid(step=GRID.step, phase=GRID.phase + shift)
        with pytest.raises(ValueError, match="crosses the period boundary"):
            reduce_weather_periods(
                start=ensure_utc(START + shift),
                end=ensure_utc(END + shift),
                samples=hourly_samples(START, range(49)),
                grid=grid,
                coverage=WeatherCoverage.ALLOW_GAPS,
            )

    @pytest.mark.parametrize(
        "start,end,grid,message",
        [
            (
                START,
                END,
                TimeGrid(step=timedelta(hours=12), phase=timedelta(0)),
                "24-hour",
            ),
            (DAY, END, GRID, "declared grid"),
            (START, END + timedelta(hours=1), GRID, "declared grid"),
            (START, START, GRID, "nonempty"),
            (END, START, GRID, "nonempty"),
        ],
    )
    def test_invalid_bounds_are_rejected(
        self, start: UtcDatetime, end: UtcDatetime, grid: TimeGrid, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            reduce_weather_periods(
                start=start,
                end=end,
                samples=(),
                grid=grid,
                coverage=WeatherCoverage.ALLOW_GAPS,
            )


class TestOperationalWeatherPeriod:
    def test_cannot_claim_value_without_matching_support(self) -> None:
        period = reduce_weather_periods(
            start=START,
            end=ensure_utc(START + GRID.step),
            samples=hourly_samples(START, range(24)),
            grid=GRID,
        )[0]
        with pytest.raises(ValueError, match="differs from contributing samples"):
            replace(period, precipitation_mm=301.0)
        with pytest.raises(OperationalWeatherCoverageError, match="gap"):
            replace(period, samples=())
        with pytest.raises(ValueError, match="declared grid"):
            replace(period, start=DAY)


def mixed_utc_samples() -> tuple[OperationalWeatherSample, ...]:
    cycle = ensure_utc(datetime(2026, 11, 1, tzinfo=UTC))
    samples = list(hourly_samples(DAY, range(24)))
    samples.extend(
        OperationalWeatherSample(
            parameter=parameter,
            source=OperationalWeatherSource.IFS,
            valid_time=ensure_utc(cycle + timedelta(hours=lead)),
            source_run=cycle,
            value=2.0,
            product=WeatherProductIdentity.CONTROL,
            interval_end=ensure_utc(cycle + timedelta(hours=lead + 6))
            if parameter == "precipitation"
            else None,
        )
        for parameter in ("precipitation", "temperature")
        for lead in (336, 342, 348, 354)
    )
    samples.append(
        OperationalWeatherSample(
            parameter="temperature",
            source=OperationalWeatherSource.IFS,
            valid_time=ensure_utc(cycle + timedelta(hours=360)),
            source_run=cycle,
            value=3.0,
            product=WeatherProductIdentity.CONTROL,
        )
    )
    # Match the pre-change parameter-major order before reversing the input.
    samples.sort(key=lambda s: s.parameter != "precipitation")
    return tuple(reversed(samples))


class TestReduceDailyWindow:
    def test_v1_mixed_window_bytes_match_prechange_base(self) -> None:
        end = ensure_utc(DAY + timedelta(days=4))
        window = reduce_daily_window(
            station_id=StationId(UUID(int=1)),
            binding_fingerprint="a" * 64,
            issue_time=end,
            start=DAY,
            end=end,
            samples=mixed_utc_samples(),
            coverage=WeatherCoverage.ALLOW_GAPS,
        )
        payload = window_bytes(window, max_bytes=2 * 1024 * 1024)
        # Measured on e5fa58ee before M2a2 changed the reduction path.
        assert hashlib.sha256(payload).hexdigest() == (
            "e3e157e9151c98032a7f71d37d56b6ddb78696a000d07b139c3adb70e23fe8e9"
        )

    def test_issue_midnight_guard_precedes_empty_window_error(self) -> None:
        with pytest.raises(ValueError, match="UTC midnight"):
            reduce_daily_window(
                station_id=StationId(UUID(int=1)),
                binding_fingerprint="a" * 64,
                issue_time=START,
                start=DAY,
                end=DAY,
                samples=(),
            )

    def test_early_gap_precedes_later_duplicate_error(self) -> None:
        later = hourly_samples(ensure_utc(DAY + timedelta(days=2)), range(24))
        with pytest.raises(OperationalWeatherCoverageError, match="gap"):
            reduce_daily_window(
                station_id=StationId(UUID(int=1)),
                binding_fingerprint="a" * 64,
                issue_time=ensure_utc(DAY + timedelta(days=3)),
                start=DAY,
                end=ensure_utc(DAY + timedelta(days=3)),
                samples=(*later, later[0]),
            )
