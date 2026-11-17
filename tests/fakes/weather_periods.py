from datetime import UTC, datetime, timedelta
from uuid import UUID

from sapphire_flow.preprocessing.recap_operational_weather import reduce_weather_periods
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    FutureWeatherRequirement,
    OperationalWeatherSample,
    OperationalWeatherSource,
    WeatherCacheKey,
    WeatherCoverage,
    WeatherProductIdentity,
)
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherRequestV2,
    OperationalWeatherWindowV2,
    WeatherCacheAttemptV2,
    WeatherPastSnapshotV2,
)
from sapphire_flow.types.time_grid import TimeGrid

ISSUE = ensure_utc(datetime(2026, 11, 17, tzinfo=UTC))
GRID = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
FIRST = ensure_utc(ISSUE - timedelta(hours=6))
SID = StationId(UUID(int=1))
KEY = WeatherCacheKey(station_id=SID, binding_fingerprint="a" * 64)


def period_attempt() -> WeatherCacheAttemptV2:
    return WeatherCacheAttemptV2(
        grid=GRID,
        eligibility_cutoff=ISSUE,
        key=KEY,
        generation=1,
        id=UUID(int=2),
        issue_time=ISSUE,
        started_at=ISSUE,
    )


def period_snapshot(
    *, days: int = 1, missing_offsets: tuple[int, ...] = ()
) -> WeatherPastSnapshotV2:
    start = ensure_utc(FIRST - days * GRID.step)
    samples = tuple(
        OperationalWeatherSample(
            parameter=parameter,
            source=OperationalWeatherSource.ERA5_LAND,
            valid_time=ensure_utc(start + timedelta(hours=hour)),
            source_run=ensure_utc((start + timedelta(hours=hour)).replace(hour=0)),
            value=1.0 if parameter == "precipitation" else 10.0,
            product=WeatherProductIdentity.REANALYSIS,
            interval_end=ensure_utc(start + timedelta(hours=hour + 1))
            if parameter == "precipitation"
            else None,
        )
        for parameter in ("precipitation", "temperature")
        for hour in range(24 * days)
        if hour // 24 not in missing_offsets
    )
    window = OperationalWeatherWindowV2(
        station_id=SID,
        binding_fingerprint=KEY.binding_fingerprint,
        grid=GRID,
        issue_time=ISSUE,
        eligibility_cutoff=ISSUE,
        start=start,
        end=FIRST,
        periods=reduce_weather_periods(
            start=start,
            end=FIRST,
            samples=samples,
            grid=GRID,
            coverage=WeatherCoverage.ALLOW_GAPS,
        ),
    )
    snapshot = make_past_snapshot(
        window=window,
        retrieved_at=ISSUE,
        snapshot_id=UUID(int=3),
        max_bytes=2 * 1024 * 1024,
    )
    assert isinstance(snapshot, WeatherPastSnapshotV2)
    return snapshot


def period_request(
    *, first: UtcDatetime = FIRST, horizons: tuple[int, int] = (2, 3)
) -> FutureWeatherRequestV2:
    return FutureWeatherRequestV2(
        station_id=SID,
        binding_fingerprint="b" * 64,
        source="ifs_ecmwf",
        grid=GRID,
        issue_time=ISSUE,
        eligibility_cutoff=ISSUE,
        first_period_start=first,
        requirements=tuple(
            FutureWeatherRequirement(
                parameter=parameter,
                member_ids=(0,),
                valid_times=tuple(
                    ensure_utc(first + GRID.step * i) for i in range(horizon)
                ),
            )
            for parameter, horizon in zip(
                ("precipitation", "temperature"), horizons, strict=True
            )
        ),
    )
