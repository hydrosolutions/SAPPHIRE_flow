from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pandas as pd
import polars as pl
import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from sapphire_flow.adapters.recap_operational import (
    RecapGatewayOperationalAdapter,
    operational_binding_fingerprint_v2,
)
from sapphire_flow.api.weather_evidence import read_weather_summary
from sapphire_flow.config.operational_weather import OperationalWeatherConfig
from sapphire_flow.config.weather_gap import WeatherGapConfig
from sapphire_flow.flows.operational_weather_refresh import (
    OperationalWeatherRefresh,
    StationWeatherRefresh,
    resolve_selected_weather_tracks,
)
from sapphire_flow.services.input_quality import selected_weather_quality_flags
from sapphire_flow.services.selected_weather_evidence import restore_selected_weather
from sapphire_flow.services.selected_weather_inputs import assemble_selected_weather
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.operational_weather_cache import PgOperationalWeatherCache
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.weather_forecast_store import PgWeatherForecastStore
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import aggregate_input_quality
from sapphire_flow.types.enums import (
    EnsembleMode,
    ForecastDataUse,
    SpatialRepresentation,
)
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
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.operational_weather import WeatherCacheKey, WeatherLegState
from sapphire_flow.types.operational_weather_v2 import (
    PastWeatherRequirementV2,
    PastWeatherUseV2,
    WeatherCacheAttemptV2,
    WeatherCacheEntryV2,
)
from sapphire_flow.types.weather import BasinAverageForecast
from sapphire_flow.types.weather_gap import WeatherGapMode
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.weather_periods import (
    FIRST,
    GRID,
    ISSUE,
    SID,
    period_request,
    period_snapshot,
)
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
    savepoint_factory,
)
from tests.unit.adapters.test_recap_operational import (
    PolygonResolver,
    RecordingApi,
    make_binding,
)
from tests.unit.services.test_period_weather_evidence import LIMIT, MID, capture
from tests.unit.services.test_recap_daily_weather import qualified_rows

if TYPE_CHECKING:
    from sapphire_flow.adapters.recap_gateway import EcmwfApiLike


class ForecastSource:
    def expected_member_ids(self, track: ForcingTrackKey) -> frozenset[int]:
        return frozenset({0})

    def fetch_requirement(
        self, track: ForcingTrackKey, stations: list, nominal_cycle: UtcDatetime
    ) -> RawFetchOutcome:
        rows = qualified_rows(nominal_cycle)
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED,
            cycle=nominal_cycle,
            stations={
                SID: BasinAverageForecast(
                    nwp_source="ifs_ecmwf",
                    cycle_time=nominal_cycle,
                    values=pl.DataFrame(
                        [
                            {
                                "valid_time": row.valid_time,
                                "parameter": row.parameter,
                                "member_id": row.member_id,
                                "value": row.value,
                            }
                            for row in rows
                        ]
                    ),
                )
            },
        )


@pytest.mark.parametrize("missing_offsets", [(), (3,)], ids=["complete", "short-gap"])
def test_refresh_storage_gap_capture_and_api_share_one_period_identity(
    db_connection: sa.Connection,
    missing_offsets: tuple[int, ...],
) -> None:
    station = make_station_config(station_id=SID)
    stations = PgStationStore(db_connection)
    stations.store_station(station)
    source = make_binding()
    resolver = PolygonResolver()
    key = WeatherCacheKey(
        station_id=SID,
        binding_fingerprint=operational_binding_fingerprint_v2(
            source, resolver.resolve(source), GRID
        ),
    )
    snapshot = period_snapshot(days=7, missing_offsets=missing_offsets)
    frames = {}
    for parameter, variable in [("precipitation", "tp"), ("temperature", "2t")]:
        rows = [
            row
            for period in snapshot.window.periods
            for row in period.samples
            if row.parameter == parameter
        ]
        frames[variable] = pd.DataFrame(
            {
                "g_225": [
                    row.value / 1000
                    if parameter == "precipitation"
                    else row.value + 273.15
                    for row in rows
                ],
                "source": [row.source.value for row in rows],
                "source_run": [row.source_run for row in rows],
                "source_meta": ["{}"] * len(rows),
            },
            index=pd.DatetimeIndex([row.valid_time for row in rows]),
        )
    api = RecordingApi(frames)
    adapter = RecapGatewayOperationalAdapter(
        api=cast("EcmwfApiLike", api),
        resolver=resolver,
        config=OperationalWeatherConfig(),
    )
    request = period_request()
    past = PastWeatherRequirementV2(
        grid=GRID,
        start=snapshot.window.start,
        end=FIRST,
        parameters=("precipitation", "temperature"),
    )
    cache = PgOperationalWeatherCache(db_connection)
    owner = OperationalWeatherRefresh(
        specs=(
            StationWeatherRefresh(
                key=key,
                source=source,
                past_requirement=past,
                future_requests=(request,),
            ),
        ),
        cache=cache,
        principal=WritePrincipal(id=None, tenant_id=station.tenant_id),
        issue_time=ISSUE,
        clock=lambda: ISSUE,
        id_gen=uuid4,
        max_past_bytes=LIMIT,
        grid=GRID,
        eligibility_cutoff=ISSUE,
    )
    owner.begin()
    assert (
        cache.fetch_period(key, grid=GRID).past_outcome.state is WeatherLegState.PENDING
    )
    owner.fetch_past(
        adapter=adapter, station_store=stations, audit_log_store=None, start=past.start
    )
    weather_store = PgWeatherForecastStore(db_connection)
    track = ForcingTrackKey(
        nwp_source="ifs_ecmwf",
        ensemble_mode=EnsembleMode.SINGLE,
        time_step=GRID.step,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset({FeatureName("precipitation"), FeatureName("temperature")}),
    )
    projection = ForcingRequired(
        key=track,
        assignment=AssignmentKey((SID, MID)),
        assignment_horizons={
            FeatureName("precipitation"): FutureSteps(value=2),
            FeatureName("temperature"): FutureSteps(value=3),
        },
    )
    forecast_source = ForecastSource()
    visited = []

    def fetch(
        track: ForcingTrackKey, sources: list, cycle: UtcDatetime
    ) -> RawFetchOutcome:
        visited.append(cycle)
        return forecast_source.fetch_requirement(track, sources, cycle)

    earliest = ensure_utc(ISSUE - timedelta(hours=60))
    resolve_selected_weather_tracks(
        projections=(projection,),
        forecast_bindings={SID: replace(source, nwp_source="ifs_ecmwf")},
        adapter=forecast_source,
        policy=ForcingResolutionPolicy(
            cycle_cadence_hours=24, max_cycle_age_hours=60, max_retries=0
        ),
        issue_time=ISSUE,
        earliest_cycle=earliest,
        clock=lambda: ISSUE,
        id_gen=uuid4,
        store=weather_store,
        owner=owner,
        fetch_candidate=fetch,
    )
    assert visited == [ISSUE, ISSUE - timedelta(days=1)]
    completed = owner.finish(earliest_cycle=earliest)[SID]
    assert isinstance(completed.entry, WeatherCacheEntryV2)
    assert isinstance(completed.attempt, WeatherCacheAttemptV2)
    retained = cache.fetch_period(key, grid=GRID)
    assert retained == completed.entry
    result = assemble_selected_weather(
        station_id=SID,
        issue_time=ISSUE,
        used_at=ISSUE,
        entry=retained,
        expected_attempt=completed.attempt,
        past_requirement=past,
        future_requirement=request,
        store=weather_store,
        earliest_cycle=earliest,
        max_past_bytes=LIMIT,
        ensemble_mode=EnsembleMode.SINGLE,
        model=FakeStationForecastModel(),
        model_id=MID,
        gap_config=WeatherGapConfig(mode=WeatherGapMode.ALWAYS),
    )
    assert result.past_dynamic["timestamp"].to_list() == [
        FIRST - offset * GRID.step for offset in range(7, 0, -1)
    ]
    assert result.past_dynamic["precipitation"].to_list() == [24.0] * 7
    assert isinstance(result.use.past, PastWeatherUseV2)
    assert len(result.use.past.gap_fills) == (2 if missing_offsets else 0)
    flags = selected_weather_quality_flags(
        StationSourceEvidence(weather_use=result.use)
    )
    assert bool(flags) == bool(missing_offsets)
    if not missing_offsets:
        assert result.past_dynamic["temperature"].to_list() == [
            period.temperature_c for period in snapshot.window.periods
        ]
    assert result.future_dynamic["timestamp"].to_list() == [
        FIRST + i * GRID.step for i in range(3)
    ]
    evidence = capture(result)
    _seed_model(db_connection, str(MID))
    artifact = _seed_artifact(db_connection, SID, MID)
    forecast = replace(
        _make_forecast(SID, MID, artifact, issued_at=ISSUE),
        evidence=evidence,
        weather_evidence_version=2,
        input_quality=aggregate_input_quality(flags),
        input_quality_flags=flags,
    )
    forecasts = PgForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    )
    forecasts.store_forecast(forecast)
    persisted = forecasts.fetch_evidence(forecast.id)
    assert persisted is not None
    restored = restore_selected_weather(persisted, model_id=MID, issue_time=ISSUE)[SID]
    assert restored == result.use
    assert (
        selected_weather_quality_flags(StationSourceEvidence(weather_use=restored))
        == flags
    )
    stored = forecasts.fetch_forecast(forecast.id)
    assert stored is not None
    assert stored.input_quality_flags == flags
    summary = read_weather_summary(
        version=2,
        forecast_id=forecast.id,
        station_id=SID,
        model_id=MID,
        issue_time=ISSUE,
        data_use=ForecastDataUse.STANDARD,
        store=forecasts,
    )
    assert summary is not None
    assert summary.past.freshness == summary.future.freshness == "current"
    assert summary.future.uses[0].sources[0].source_run == ISSUE - timedelta(days=1)
    assert summary.past.uses[0].snapshot_sha256 == retained.past_snapshot.payload_sha256
    with pytest.raises(HTTPException, match="unavailable"):
        read_weather_summary(
            version=1,
            forecast_id=forecast.id,
            station_id=SID,
            model_id=MID,
            issue_time=ISSUE,
            data_use=ForecastDataUse.STANDARD,
            store=forecasts,
        )
