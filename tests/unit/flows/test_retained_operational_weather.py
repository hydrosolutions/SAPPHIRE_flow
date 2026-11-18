from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from sapphire_flow.flows.operational_weather_refresh import (
    OperationalWeatherRefresh,
    StationWeatherRefresh,
    resolve_selected_weather_tracks,
)
from sapphire_flow.services.operational_weather_projection import project_future_weather
from sapphire_flow.services.retained_operational_weather import RetainedPastRequirement
from sapphire_flow.store.operational_weather_codec import (
    decode_cache_entry,
    encode_cache_entry,
    make_past_snapshot,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleMode, SpatialRepresentation
from sapphire_flow.types.forcing_track import (
    AssignmentKey,
    FeatureName,
    ForcingRequired,
    ForcingResolutionPolicy,
    ForcingTrackKey,
    FutureSteps,
    RawFetchOutcome,
    RawFetchStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.operational_weather import (
    PastWeatherRequirement,
    WeatherCacheCompletion,
    WeatherCacheKey,
    WeatherFailureReason,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherPastSnapshot,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore, FakeWeatherForecastStore
from tests.fakes.operational_weather import ISSUE, FakeOperationalWeatherCache
from tests.unit.adapters.test_recap_operational import make_binding
from tests.unit.flows.test_run_forecast_cycle import _FakeCandidateAwareSource
from tests.unit.services.test_retained_operational_weather import (
    future_request,
)
from tests.unit.services.test_retained_operational_weather import (
    retained_snapshot as retained_snapshot,
)


def test_oversized_verified_station_preserves_old_pair_without_failing_other_station(
    retained_snapshot: WeatherPastSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.store.operational_weather_codec import window_bytes
    from tests.unit.flows.test_combined_operational_ingestion import (
        RecordingOperationalSource,
    )

    stations = FakeStationStore()
    cache = FakeOperationalWeatherCache(station_store=stations)
    small = replace(
        retained_snapshot.window,
        start=ensure_utc(ISSUE - timedelta(days=1)),
        days=tuple(
            day
            for day in retained_snapshot.window.days
            if day.day >= ISSUE - timedelta(days=1)
        ),
    )
    limit = len(window_bytes(small, max_bytes=2 * 1024 * 1024)) + 1
    specs = []
    windows = {}
    old = None
    for index in range(2):
        sid = StationId(UUID(int=index + 1))
        station = make_station_config(station_id=sid)
        stations.store_station(station)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        key = WeatherCacheKey(
            station_id=sid, binding_fingerprint=small.binding_fingerprint
        )
        specs.append(
            StationWeatherRefresh(
                key=key,
                source=replace(make_binding(), station_id=sid),
                past_requirement=PastWeatherRequirement(
                    start=small.start,
                    end=ISSUE,
                    parameters=("precipitation", "temperature"),
                ),
                future_requests=(),
            )
        )
        windows[sid] = replace(
            retained_snapshot.window if index == 0 else small, station_id=sid
        )
        if index == 0:
            old = make_past_snapshot(
                window=replace(small, station_id=sid),
                retrieved_at=ISSUE,
                snapshot_id=uuid4(),
                max_bytes=limit,
            )
            seed = cache.begin(
                key=key,
                principal=principal,
                attempt_id=uuid4(),
                issue_time=ISSUE,
                started_at=ISSUE,
            )
            cache.finish(
                attempt=seed,
                principal=principal,
                earliest_cycle=ISSUE,
                completion=WeatherCacheCompletion(
                    finished_at=ISSUE,
                    past_outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
                    past_snapshot=old,
                    future_attempts=(),
                ),
            )
    adapter = RecordingOperationalSource()
    monkeypatch.setattr(adapter, "fetch_windows", lambda **_kwargs: windows)
    owner = OperationalWeatherRefresh(
        specs=tuple(specs),
        cache=cache,
        principal=principal,
        issue_time=ISSUE,
        clock=lambda: ISSUE,
        id_gen=uuid4,
        max_past_bytes=limit,
    )
    owner.begin()
    owner.fetch_past(
        adapter=adapter, station_store=stations, audit_log_store=None, start=small.start
    )
    views = owner.finish(earliest_cycle=ISSUE)
    assert views[specs[0].key.station_id].entry.past_snapshot == old
    assert (
        views[specs[0].key.station_id].entry.past_outcome.state
        is WeatherLegState.UNAVAILABLE
    )
    assert (
        views[specs[1].key.station_id].entry.past_outcome.state
        is WeatherLegState.SUCCESS
    )


def test_expiring_donor_cannot_make_a_replacement_look_usable(
    retained_snapshot: WeatherPastSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.services.operational_weather_projection import (
        future_receipt_scope,
    )
    from sapphire_flow.services.operational_weather_proof import prove_future_receipt
    from sapphire_flow.types.operational_weather import (
        FutureWeatherAttempt,
        OperationalWeatherDay,
    )
    from tests.unit.flows.test_combined_operational_ingestion import (
        RecordingOperationalSource,
    )
    from tests.unit.services.test_recap_daily_weather import qualified_rows

    sid = retained_snapshot.window.station_id
    station = make_station_config(station_id=sid)
    stations = FakeStationStore()
    stations.store_station(station)
    principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
    cache = FakeOperationalWeatherCache(station_store=stations)
    archive = FakeWeatherForecastStore()
    rows = [replace(row, station_id=sid) for row in qualified_rows(ISSUE)]
    archive.store_weather_forecasts(rows)
    key = WeatherCacheKey(
        station_id=sid, binding_fingerprint=retained_snapshot.window.binding_fingerprint
    )
    old = make_past_snapshot(
        window=replace(
            retained_snapshot.window,
            end=ISSUE,
            days=tuple(day for day in retained_snapshot.window.days if day.day < ISSUE),
        ),
        retrieved_at=ISSUE,
        snapshot_id=uuid4(),
        max_bytes=2 * 1024 * 1024,
    )
    old_request = future_request(old, days_later=0)
    scope = future_receipt_scope(old_request, cycle_time=ISSUE)
    receipt = prove_future_receipt(
        scope=scope, cycle_time=ISSUE, validated_at=ISSUE, fetched=rows, readback=rows
    )
    seed = cache.begin(
        key=key,
        principal=principal,
        attempt_id=uuid4(),
        issue_time=ISSUE,
        started_at=ISSUE,
        future_scopes=(old_request,),
    )
    cache.finish(
        attempt=seed,
        principal=principal,
        earliest_cycle=ISSUE,
        completion=WeatherCacheCompletion(
            finished_at=ISSUE,
            past_outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
            past_snapshot=old,
            future_attempts=(
                FutureWeatherAttempt(
                    scope=old_request,
                    outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
                    resolved_cycle=ISSUE,
                ),
            ),
            future_receipts=(receipt,),
        ),
    )
    request = future_request(retained_snapshot, days_later=3)
    issue = request.issue_time
    now = ensure_utc(issue + timedelta(hours=6))
    earliest = ensure_utc(now - timedelta(hours=60))
    past = PastWeatherRequirement(
        start=ensure_utc(issue - timedelta(days=30)),
        end=issue,
        parameters=("precipitation", "temperature"),
    )
    spec = StationWeatherRefresh(
        key=key,
        source=make_binding(),
        past_requirement=past,
        future_requests=(request,),
        retained_past_requirements=(
            RetainedPastRequirement(
                past=past, fill_parameters=frozenset(past.parameters), max_gap_days=1
            ),
        ),
        retained_future_bindings=frozenset({request.binding_fingerprint}),
    )
    fresh = replace(
        retained_snapshot.window,
        issue_time=issue,
        days=tuple(
            OperationalWeatherDay(
                day=day.day, precipitation_mm=None, temperature_c=None, samples=()
            )
            if day.day == issue - timedelta(days=1)
            else day
            for day in retained_snapshot.window.days
        ),
    )
    adapter = RecordingOperationalSource()
    monkeypatch.setattr(adapter, "fetch_windows", lambda **_kwargs: {sid: fresh})
    owner = OperationalWeatherRefresh(
        specs=(spec,),
        cache=cache,
        principal=principal,
        issue_time=issue,
        clock=lambda: now,
        id_gen=uuid4,
        max_past_bytes=2 * 1024 * 1024,
        weather_store=archive,
        earliest_cycle=earliest,
    )
    owner.begin()
    owner.fetch_past(
        adapter=adapter, station_store=stations, audit_log_store=None, start=past.start
    )
    completed = owner.finish(earliest_cycle=earliest)[sid]
    assert completed.entry.past_snapshot == old
    assert completed.entry.past_outcome.reason is WeatherFailureReason.NO_USABLE_PRODUCT


@pytest.mark.parametrize("unseeded_station", [False, True])
def test_whole_gateway_failure_recovers_full_current_horizon_after_restart(
    retained_snapshot: WeatherPastSnapshot,
    unseeded_station: bool,
) -> None:
    stations = FakeStationStore()
    cache = FakeOperationalWeatherCache(station_store=stations)
    archive = FakeWeatherForecastStore()
    specs = []
    projections = []
    sources = {}
    issue = ensure_utc(ISSUE + timedelta(days=2))
    now = ensure_utc(issue + timedelta(hours=6))
    earliest = ensure_utc(now - timedelta(hours=60))
    key = ForcingTrackKey(
        nwp_source="ifs_ecmwf",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=timedelta(days=1),
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset({FeatureName("precipitation"), FeatureName("temperature")}),
    )
    for index in range(17):
        sid = StationId(UUID(int=index + 1))
        station = make_station_config(station_id=sid)
        stations.store_station(station)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        snapshot = make_past_snapshot(
            window=replace(retained_snapshot.window, station_id=sid),
            retrieved_at=ISSUE,
            snapshot_id=uuid4(),
            max_bytes=2 * 1024 * 1024,
        )
        cache_key = WeatherCacheKey(
            station_id=sid, binding_fingerprint=snapshot.window.binding_fingerprint
        )
        seed = cache.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        entry = cache.finish(
            attempt=seed,
            principal=principal,
            earliest_cycle=earliest,
            completion=WeatherCacheCompletion(
                finished_at=ISSUE,
                past_outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
                past_snapshot=snapshot,
                future_attempts=(),
            ),
        )
        cache.entries[cache_key] = decode_cache_entry(
            encode_cache_entry(entry, max_past_bytes=2 * 1024 * 1024),
            max_past_bytes=2 * 1024 * 1024,
        )
        request = future_request(snapshot)
        past = PastWeatherRequirement(
            start=ensure_utc(issue - timedelta(days=30)),
            end=issue,
            parameters=("precipitation", "temperature"),
        )
        source = replace(make_binding(), station_id=sid)
        sources[sid] = replace(source, nwp_source="ifs_ecmwf")
        specs.append(
            StationWeatherRefresh(
                key=cache_key,
                source=source,
                past_requirement=past,
                future_requests=(request,),
                retained_past_requirements=(
                    RetainedPastRequirement(
                        past=past, fill_parameters=frozenset(), max_gap_days=1
                    ),
                ),
                retained_future_bindings=frozenset({request.binding_fingerprint}),
            )
        )
        projections.append(
            ForcingRequired(
                key=key,
                assignment=AssignmentKey((sid, ModelId("cmal_fixture"))),
                assignment_horizons={
                    name: FutureSteps(value=10) for name in key.features
                },
            )
        )
    if unseeded_station:
        del cache.entries[specs[-1].key]
    owner = OperationalWeatherRefresh(
        specs=tuple(specs),
        cache=cache,
        principal=principal,
        issue_time=issue,
        clock=lambda: now,
        id_gen=uuid4,
        max_past_bytes=2 * 1024 * 1024,
        weather_store=archive,
        earliest_cycle=earliest,
    )
    owner.begin()
    owner.fail_past(
        WeatherLegOutcome(
            state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
        )
    )
    adapter = _FakeCandidateAwareSource()
    adapter.expected_member_ids = lambda _key: frozenset({0})
    resolve_selected_weather_tracks(
        projections=tuple(projections),
        forecast_bindings=sources,
        adapter=adapter,
        policy=ForcingResolutionPolicy(
            cycle_cadence_hours=6, max_cycle_age_hours=60, max_retries=0
        ),
        issue_time=issue,
        earliest_cycle=earliest,
        clock=lambda: now,
        id_gen=uuid4,
        store=archive,
        owner=owner,
        fetch_candidate=lambda _key, _sources, cycle: RawFetchOutcome(
            status=RawFetchStatus.ABSENT_AT_CYCLE, cycle=cycle, stations={}
        ),
    )
    completed = owner.finish(earliest_cycle=earliest)
    assert len(completed) == 17
    for spec in specs:
        view = completed[spec.key.station_id]
        if unseeded_station and spec is specs[-1]:
            assert view.entry.past_snapshot is None
            assert (
                view.entry.past_outcome.reason is WeatherFailureReason.NO_USABLE_PRODUCT
            )
            assert view.entry.future_receipts == ()
            continue
        assert view.entry.past_snapshot.retrieved_at == ISSUE
        assert (
            view.entry.future_attempts[0].outcome.state is WeatherLegState.UNAVAILABLE
        )
        use, rows = project_future_weather(
            view.entry,
            expected_attempt=view.attempt,
            requirement=spec.future_requests[0],
            store=archive,
            earliest_cycle=earliest,
        )
        assert use.freshness is WeatherFreshness.STALE
        assert use.receipt.cycle_time == ISSUE
        native = {
            (sample.parameter, sample.valid_time): sample
            for day in retained_snapshot.window.days
            for sample in day.samples
        }
        assert all(
            row.value == native[(row.parameter, row.valid_time)].value for row in rows
        )
        assert max(row.valid_time for row in rows) >= issue + timedelta(days=9)
