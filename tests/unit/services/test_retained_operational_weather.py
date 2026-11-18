from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pandas as pd
import pytest

from sapphire_flow.adapters.recap_operational import RecapGatewayOperationalAdapter
from sapphire_flow.config.operational_weather import OperationalWeatherConfig
from sapphire_flow.services.retained_operational_weather import (
    RetainedPastRequirement,
    SnapshotSupport,
    retained_future_samples,
    retained_tail_cycle,
    snapshot_support,
)
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.operational_weather import (
    FutureWeatherRequest,
    FutureWeatherRequirement,
    OperationalWeatherDay,
    PastWeatherRequirement,
    WeatherPastSnapshot,
)
from tests.unit.adapters.test_recap_operational import (
    FIXTURES,
    ISSUE,
    START,
    PolygonResolver,
    RecordingApi,
    make_binding,
)


@pytest.fixture
def retained_snapshot() -> WeatherPastSnapshot:
    source = make_binding()
    adapter = RecapGatewayOperationalAdapter(
        api=RecordingApi(
            {
                name: pd.read_parquet(FIXTURES / f"{parameter}.parquet")
                for name, parameter in (("tp", "precipitation"), ("2t", "temperature"))
            }
        ),
        resolver=PolygonResolver(),
        config=OperationalWeatherConfig(),
    )
    window = adapter.fetch_windows(
        sources=[source], issue_time=ISSUE, start=START, end=ISSUE
    )[source.station_id]
    return make_past_snapshot(
        window=window,
        retrieved_at=ISSUE,
        snapshot_id=uuid4(),
        max_bytes=2 * 1024 * 1024,
    )


def future_request(
    snapshot: WeatherPastSnapshot, *, days_later: int = 2
) -> FutureWeatherRequest:
    issue = ensure_utc(ISSUE + timedelta(days=days_later))
    return FutureWeatherRequest(
        station_id=snapshot.window.station_id,
        binding_fingerprint="b" * 64,
        source="ifs_ecmwf",
        issue_time=issue,
        requirements=tuple(
            FutureWeatherRequirement(
                parameter=name,
                member_ids=(0,),
                valid_times=tuple(
                    ensure_utc(issue + timedelta(days=i)) for i in range(10)
                ),
            )
            for name in ("precipitation", "temperature")
        ),
    )


def support(snapshot: WeatherPastSnapshot, *, days_later: int = 2) -> SnapshotSupport:
    request = future_request(snapshot, days_later=days_later)
    return snapshot_support(
        snapshot,
        binding_fingerprint=snapshot.window.binding_fingerprint,
        requirements=(
            RetainedPastRequirement(
                past=PastWeatherRequirement(
                    start=ensure_utc(request.issue_time - timedelta(days=30)),
                    end=request.issue_time,
                    parameters=("precipitation", "temperature"),
                ),
                fill_parameters=frozenset({"precipitation", "temperature"}),
                max_gap_days=1,
            ),
        ),
        future_requests=(request,),
        issue_time=request.issue_time,
        max_past_lead=timedelta(hours=72),
        donors={},
    )


class TestRetainedOperationalWeather:
    @pytest.mark.parametrize("days_later", [1, 2, 3])
    def test_native_tail_becomes_past_without_interpolation(
        self, retained_snapshot: WeatherPastSnapshot, days_later: int
    ) -> None:
        assert (
            support(retained_snapshot, days_later=days_later) is SnapshotSupport.NATIVE
        )

    def test_lead_limit_refuses_even_when_gap_filling_is_allowed(
        self, retained_snapshot: WeatherPastSnapshot
    ) -> None:
        assert support(retained_snapshot, days_later=4) is SnapshotSupport.UNUSABLE

    def test_single_internal_gap_keeps_existing_fill_policy(
        self, retained_snapshot: WeatherPastSnapshot
    ) -> None:
        days = retained_snapshot.window.days
        hole = OperationalWeatherDay(
            day=days[10].day, precipitation_mm=None, temperature_c=None, samples=()
        )
        snapshot = replace(
            retained_snapshot,
            window=replace(
                retained_snapshot.window, days=days[:10] + (hole,) + days[11:]
            ),
        )
        assert support(snapshot) is SnapshotSupport.FILLED
        second = replace(hole, day=days[11].day)
        snapshot = replace(
            snapshot,
            window=replace(
                snapshot.window, days=days[:10] + (hole, second) + days[12:]
            ),
        )
        assert support(snapshot) is SnapshotSupport.UNUSABLE

    def test_future_uses_one_real_cycle_and_requires_the_entire_horizon(
        self, retained_snapshot: WeatherPastSnapshot
    ) -> None:
        request = future_request(retained_snapshot)
        assert retained_tail_cycle(retained_snapshot, (request,)) == ISSUE
        samples = retained_future_samples(retained_snapshot, request, cycle=ISSUE)
        assert samples and {sample.source_run for sample in samples} == {ISSUE}
        assert max(
            sample.valid_time for sample in samples
        ) >= request.issue_time + timedelta(days=9)
        truncated = replace(
            retained_snapshot,
            window=replace(
                retained_snapshot.window,
                days=retained_snapshot.window.days[:-6],
                end=retained_snapshot.window.end - timedelta(days=6),
            ),
        )
        assert retained_future_samples(truncated, request, cycle=ISSUE) is None
