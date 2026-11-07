from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    OperationalWeatherDay,
    OperationalWeatherSample,
    OperationalWeatherSource,
    OperationalWeatherWindow,
    WeatherProductIdentity,
)

ISSUE = ensure_utc(datetime(2026, 11, 6, tzinfo=UTC))


def ifs_day(offset: int = 0) -> OperationalWeatherDay:
    day = ensure_utc(ISSUE + timedelta(days=offset))
    step = 3 if offset < 6 else 6
    samples = tuple(
        OperationalWeatherSample(
            parameter=parameter,
            source=OperationalWeatherSource.IFS,
            valid_time=ensure_utc(day + timedelta(hours=hour)),
            source_run=ISSUE,
            value=1.0 if parameter == "precipitation" else 10.0,
            product=WeatherProductIdentity.CONTROL,
            interval_end=(
                ensure_utc(day + timedelta(hours=hour + step))
                if parameter == "precipitation"
                else None
            ),
        )
        for parameter in ("precipitation", "temperature")
        for hour in range(0, 24, step)
    )
    return OperationalWeatherDay(
        day=day, precipitation_mm=24 / step, temperature_c=10.0, samples=samples
    )


class TestOperationalWeatherSample:
    def test_144_hour_boundary_uses_six_hour_start_interval(self) -> None:
        sample = ifs_day(6).samples[0]
        assert sample.interval_end == ISSUE + timedelta(hours=150)

    def test_final_long_cycle_interval_closes_at_360_hours(self) -> None:
        sample = ifs_day(14).samples[3]
        assert sample.interval_end == ISSUE + timedelta(hours=360)
        with pytest.raises(ValueError, match="qualified IFS horizon"):
            replace(
                sample,
                valid_time=ensure_utc(ISSUE + timedelta(hours=360)),
                interval_end=ensure_utc(ISSUE + timedelta(hours=366)),
            )

    def test_rejects_double_shifted_or_wrong_interval(self) -> None:
        sample = ifs_day(6).samples[0]
        with pytest.raises(ValueError, match="interval conflicts"):
            replace(
                sample, interval_end=ensure_utc(sample.valid_time + timedelta(hours=3))
            )

    @pytest.mark.parametrize("value", [-0.000001, float("nan"), float("inf")])
    def test_rejects_negative_or_nonfinite_precipitation(self, value: float) -> None:
        with pytest.raises(ValueError, match="nonnegative|finite"):
            replace(ifs_day().samples[0], value=value)

    def test_rejects_conflicting_product_identity(self) -> None:
        with pytest.raises(ValueError, match="product conflicts"):
            replace(ifs_day().samples[0], product=WeatherProductIdentity.REANALYSIS)

    @pytest.mark.parametrize("hour", [6, 12, 18])
    def test_short_cycles_end_at_72_hours(self, hour: int) -> None:
        run = ensure_utc(ISSUE + timedelta(hours=hour))
        sample = replace(
            ifs_day().samples[0],
            source_run=run,
            valid_time=ensure_utc(run + timedelta(hours=69)),
            interval_end=ensure_utc(run + timedelta(hours=72)),
        )
        assert sample.sample_step() == timedelta(hours=3)
        with pytest.raises(ValueError, match="qualified IFS horizon"):
            replace(
                sample,
                valid_time=ensure_utc(run + timedelta(hours=72)),
                interval_end=ensure_utc(run + timedelta(hours=75)),
            )

    def test_temperature_cannot_claim_interval(self) -> None:
        sample = ifs_day().samples[-1]
        with pytest.raises(ValueError, match="snapshot"):
            replace(sample, interval_end=ensure_utc(ISSUE + timedelta(days=1)))


class TestOperationalWeatherDay:
    @pytest.mark.parametrize("offset", [0, 5, 6, 9, 14])
    def test_complete_qualified_grid(self, offset: int) -> None:
        day = ifs_day(offset)
        assert day.precipitation_mm == (8.0 if offset < 6 else 4.0)

    def test_missing_final_precipitation_interval_is_not_invented(self) -> None:
        day = ifs_day(9)
        with pytest.raises(ValueError, match="gap|complete UTC grid"):
            replace(day, samples=day.samples[:3] + day.samples[4:])

    def test_duplicate_temperature_rejected(self) -> None:
        day = ifs_day()
        with pytest.raises(ValueError, match="duplicate"):
            replace(day, samples=day.samples + (day.samples[-1],))

    def test_wrong_reduced_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="differs"):
            replace(ifs_day(), temperature_c=11.0)


class TestOperationalWeatherWindow:
    def test_complete_window(self) -> None:
        window = OperationalWeatherWindow(
            station_id=StationId(uuid4()),
            binding_fingerprint="a" * 64,
            issue_time=ISSUE,
            start=ISSUE,
            end=ensure_utc(ISSUE + timedelta(days=1)),
            days=(ifs_day(),),
        )
        assert len(window.days) == 1

    def test_nonmidnight_issue_rejected(self) -> None:
        with pytest.raises(ValueError, match="UTC midnight"):
            OperationalWeatherWindow(
                station_id=StationId(uuid4()),
                binding_fingerprint="a" * 64,
                issue_time=ensure_utc(ISSUE + timedelta(hours=6)),
                start=ISSUE,
                end=ensure_utc(ISSUE + timedelta(days=1)),
                days=(ifs_day(),),
            )

    def test_future_cycle_leakage_rejected(self) -> None:
        with pytest.raises(ValueError, match="after forecast issue"):
            OperationalWeatherWindow(
                station_id=StationId(uuid4()),
                binding_fingerprint="a" * 64,
                issue_time=ensure_utc(ISSUE - timedelta(days=1)),
                start=ISSUE,
                end=ensure_utc(ISSUE + timedelta(days=1)),
                days=(ifs_day(),),
            )
