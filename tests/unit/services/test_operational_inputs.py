from __future__ import annotations

import random
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from sapphire_flow.services.operational_inputs import (
    _aggregate_nwp_records_to_time_step,
    _AggregatedNwpPoint,
    _filter_and_cap_daily_records,
    assemble_station_operational_inputs,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WarmUpSource,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.conftest import (
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

_STEP = timedelta(hours=24)
_ISSUE = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
_CYCLE = ensure_utc(datetime(2026, 1, 9, 18, tzinfo=UTC))  # 6h before issue
_NOW = ensure_utc(datetime(2026, 1, 10, 1, tzinfo=UTC))  # 1h after issue
_NWP_SOURCE = "icon_ch2_eps"
_MODEL_ID = ModelId("fake_station_model")
_LOOKBACK = 5  # days worth for test (model has 720 steps default but we patch)


def _utc(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _clock() -> UtcDatetime:
    return _NOW


def _make_nwp_records(
    station_id: StationId,
    cycle_time: UtcDatetime,
    start: UtcDatetime,
    n_steps: int,
    parameters: list[str] | None = None,
    n_members: int = 3,
) -> list[WeatherForecastRecord]:
    params = parameters or ["precipitation", "temperature"]
    records = []
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(start.timestamp() + (step + 1) * 3600, tz=UTC)
        )
        for param in params:
            for m in range(n_members):
                records.append(
                    WeatherForecastRecord(
                        id=uuid4(),
                        station_id=station_id,
                        nwp_source=_NWP_SOURCE,
                        cycle_time=cycle_time,
                        valid_time=vt,
                        parameter=param,
                        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                        band_id=None,
                        member_id=m,
                        value=float(step + m),
                        created_at=_NOW,
                    )
                )
    return records


def _seed_forcing(
    source: FakeWeatherReanalysisSource,
    station_id: StationId,
    start: UtcDatetime,
    n_days: int,
    parameters: list[str] | None = None,
) -> None:
    params = parameters or ["precipitation", "temperature"]
    records = []
    for i in range(n_days * 24):
        ts = ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 3600, tz=UTC))
        for param in params:
            records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter=param,
                    valid_time=ts,
                    value=float(i % 10),
                )
            )
    source.set_records(records)


class _SmallModelRequirements:
    """Minimal wrapper to override lookback_steps for faster tests."""

    from sapphire_flow.types.enums import ArtifactScope
    from sapphire_flow.types.model import ModelDataRequirements

    artifact_scope = ArtifactScope.STATION

    data_requirements = FakeStationForecastModel.data_requirements.__class__(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=10,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )

    def train(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return b""

    def predict(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return ({}, None)

    def serialize_artifact(self, artifact):  # type: ignore[no-untyped-def]
        return b""

    def deserialize_artifact(self, raw):  # type: ignore[no-untyped-def]
        return raw


def _make_model() -> _SmallModelRequirements:
    return _SmallModelRequirements()


def _make_stores_and_sources(
    station_id: StationId,
    with_nwp: bool = True,
    with_obs: bool = True,
    with_state: bool = True,
    state_age_hours: float = 1.0,
    n_obs: int = 20,
    n_nwp_steps: int = 120,
) -> tuple:
    station_store = FakeStationStore()
    basin_store = FakeBasinStore()
    obs_store = FakeObservationStore()
    nwp_store = FakeWeatherForecastStore()
    state_store = FakeModelStateStore()
    reanalysis = FakeWeatherReanalysisSource()

    station_cfg = make_station_config(station_id=station_id)
    station_store.store_station(station_cfg)
    from sapphire_flow.types.station import StationWeatherSource

    station_store.store_weather_source(
        StationWeatherSource(
            station_id=station_id,
            nwp_source=_NWP_SOURCE,
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.FORECAST,
        )
    )

    if with_obs:
        obs_start = ensure_utc(_ISSUE - n_obs * timedelta(hours=1))

        obs = make_observations(
            n=n_obs,
            station_id=station_id,
            parameter="discharge",
            start=obs_start,
            interval=timedelta(hours=1),
        )
        obs_store.store_observations(obs)
        # Seed reanalysis for past_dynamic
        _seed_forcing(reanalysis, station_id, obs_start, n_days=2)

    if with_nwp:
        nwp_records = _make_nwp_records(
            station_id=station_id,
            cycle_time=_CYCLE,
            start=_ISSUE,
            n_steps=n_nwp_steps,
        )
        nwp_store.store_weather_forecasts(nwp_records)

    if with_state:
        state_time = ensure_utc(
            datetime.fromtimestamp(_NOW.timestamp() - state_age_hours * 3600, tz=UTC)
        )
        state_store.store_state(station_id, _MODEL_ID, state_time, b"state_bytes")

    return station_store, basin_store, obs_store, nwp_store, state_store, reanalysis


class TestAssembleStationOperationalInputs:
    def test_happy_path_returns_inputs_and_fresh_metadata(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, state_age_hours=1.0)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        inputs, metadata = result
        assert inputs.station_id == sid
        assert inputs.issue_time == _ISSUE
        assert inputs.forecast_horizon_steps == 120
        assert not inputs.data.past_targets.is_empty()
        assert not inputs.data.future_dynamic.is_empty()
        assert metadata.warm_up_source == WarmUpSource.FRESH
        assert metadata.warm_up_state_age_hours is not None
        assert metadata.warm_up_state_age_hours < 24.0
        assert metadata.prior_state == b"state_bytes"
        assert metadata.nwp_age_hours > 0

    def test_missing_nwp_returns_none(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, with_nwp=False)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is None

    def test_missing_observations_returns_inputs_with_none_staleness(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, with_obs=False)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        _, metadata = result
        assert metadata.observation_staleness_hours is None

    def test_stale_warm_up_state_returns_snapshot(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, state_age_hours=30.0)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        _, metadata = result
        assert metadata.warm_up_source == WarmUpSource.SNAPSHOT
        assert metadata.warm_up_state_age_hours is not None
        assert metadata.warm_up_state_age_hours >= 24.0

    def test_no_warm_up_state_returns_cold_start(self) -> None:
        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = (
            _make_stores_and_sources(sid, with_state=False)
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=reanalysis,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        _, metadata = result
        assert metadata.warm_up_source == WarmUpSource.COLD_START
        assert metadata.warm_up_state_age_hours is None
        assert metadata.prior_state is None

    def test_empty_past_dynamic_features_skips_reanalysis(self) -> None:
        from sapphire_flow.types.enums import ArtifactScope
        from sapphire_flow.types.model import ModelDataRequirements

        class _NoPastDynamicModel:
            artifact_scope = ArtifactScope.STATION
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset({"precipitation"}),
                static_features=frozenset(),
                supported_time_steps=frozenset({timedelta(hours=1)}),
                lookback_steps=10,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )

            def train(self, *a, **kw):  # type: ignore[no-untyped-def]
                return b""

            def predict(self, *a, **kw):  # type: ignore[no-untyped-def]
                return ({}, None)

            def serialize_artifact(self, a):  # type: ignore[no-untyped-def]
                return b""

            def deserialize_artifact(self, r):  # type: ignore[no-untyped-def]
                return r

        sid = StationId(uuid4())
        station_store, basin_store, obs_store, nwp_store, state_store, _ = (
            _make_stores_and_sources(sid)
        )
        # Use a reanalysis that would fail if called — pass empty one
        empty_reanalysis = FakeWeatherReanalysisSource(records=[])

        # Seed NWP with only "precipitation"
        nwp_store2 = FakeWeatherForecastStore()
        nwp_records = _make_nwp_records(
            station_id=sid,
            cycle_time=_CYCLE,
            start=_ISSUE,
            n_steps=10,
            parameters=["precipitation"],
            n_members=1,
        )
        nwp_store2.store_weather_forecasts(nwp_records)

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=_NoPastDynamicModel(),  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=empty_reanalysis,
            weather_forecast_store=nwp_store2,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=10,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        inputs, _ = result
        assert inputs.data.past_dynamic.is_empty()


class _SourceSpyingForcingStore:
    """Wraps ``FakeHistoricalForcingStore``, recording every ``source`` value
    a caller queries ``fetch_forcing`` with. Used to prove the FORECAST
    binding's ``nwp_source`` is never dereferenced against the forcing store
    — regardless of which layer (call site or adapter) would otherwise be
    responsible for the guard."""

    def __init__(self) -> None:
        from tests.fakes.fake_stores import FakeHistoricalForcingStore

        self._inner = FakeHistoricalForcingStore()
        self.queried_sources: list[str] = []

    def store_forcing(self, records: object) -> None:
        self._inner.store_forcing(records)  # type: ignore[arg-type]

    def fetch_forcing(self, *, station_id: object, source: str, **kwargs: object):  # type: ignore[no-untyped-def]
        self.queried_sources.append(source)
        return self._inner.fetch_forcing(
            station_id=station_id,
            source=source,
            **kwargs,  # type: ignore[arg-type]
        )


class TestReanalysisPathExcludesForecastBinding:
    def test_forecast_bindings_nwp_source_never_queried_against_forcing_store(
        self,
    ) -> None:
        # A station carries TWO BASIN_AVERAGE bindings: FORECAST (icon_ch2_eps)
        # and REANALYSIS (camels-ch). Routed through the real production chain
        # — assemble_station_operational_inputs -> select_reanalysis_source
        # (mode="single") -> StoreBackedReanalysisSource — the FORECAST
        # binding's nwp_source must NEVER reach the forcing store's
        # fetch_forcing. Soundness: fails against an implementation that
        # passes the unfiltered fetch_weather_sources() list through to
        # forcing_source.fetch_reanalysis, since the spy would then observe a
        # fetch_forcing(source="icon_ch2_eps") call.
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source,
        )
        from sapphire_flow.types.station import StationWeatherSource

        sid = StationId(uuid4())
        model = _make_model()
        station_store, basin_store, obs_store, nwp_store, state_store, _ = (
            _make_stores_and_sources(sid)
        )
        # _make_stores_and_sources already registered a FORECAST binding
        # (icon_ch2_eps); add a REANALYSIS binding for the same station.
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="camels-ch",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )

        spy_store = _SourceSpyingForcingStore()
        obs_start = ensure_utc(_ISSUE - 20 * timedelta(hours=1))
        spy_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=sid,
                    source="camels-ch",
                    parameter=param,
                    valid_time=ensure_utc(
                        datetime.fromtimestamp(obs_start.timestamp() + i * 3600, tz=UTC)
                    ),
                    value=float(i % 10),
                )
                for i in range(48)
                for param in ("precipitation", "temperature")
            ]
        )
        forcing_source = select_reanalysis_source(
            forcing_store=spy_store,  # type: ignore[arg-type]
            mode="single",
        )

        result = assemble_station_operational_inputs(
            station_id=sid,
            model=model,
            model_id=_MODEL_ID,
            issue_time=_ISSUE,
            cycle_time=_CYCLE,
            nwp_source=_NWP_SOURCE,
            forcing_source=forcing_source,
            weather_forecast_store=nwp_store,
            obs_store=obs_store,
            station_store=station_store,
            basin_store=basin_store,
            model_state_store=state_store,
            clock=_clock,
            forecast_horizon_steps=120,
            time_step=timedelta(hours=1),
        )

        assert result is not None
        assert "icon_ch2_eps" not in spy_store.queried_sources
        assert spy_store.queried_sources == ["camels-ch"]


# --------------------------------------------------------------------------- #
# M3: hourly per-member ICON forcing -> DAILY aggregation before the pivot.
#
# Precip aggregates by SUM, temperature by MEAN, keyed on (parameter, member_id,
# UTC-calendar-day). All 21 members are preserved; buckets sit on UTC midnight.
# RED until assemble_station_operational_inputs aggregates the hourly future block
# to the model's daily time_step.
# --------------------------------------------------------------------------- #

_AGG_CYCLE = ensure_utc(datetime(2026, 1, 9, 20, tzinfo=UTC))
_AGG_MEMBERS = 21  # ICON-CH2-EPS member_id 0..20
# Two UTC days plus a partial first day (22:00, 23:00) and a partial last day.
_AGG_HOURS: list[datetime] = (
    [datetime(2026, 1, 9, h, tzinfo=UTC) for h in (22, 23)]
    + [datetime(2026, 1, 10, h, tzinfo=UTC) for h in range(24)]
    + [datetime(2026, 1, 11, h, tzinfo=UTC) for h in (0, 1, 2)]
)


def _agg_precip(member: int, hour: int) -> float:
    # Distinct per member AND per hour so SUM is not confusable with count/mean.
    return float(member) * 100.0 + float(hour) + 1.0


def _agg_temp(member: int, hour: int) -> float:
    return float(member) * 10.0 + float(hour) * 0.5


def _hourly_ensemble_records(station_id: StationId) -> list[WeatherForecastRecord]:
    records: list[WeatherForecastRecord] = []
    for ts in _AGG_HOURS:
        vt = ensure_utc(ts)
        for m in range(_AGG_MEMBERS):
            records.append(
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=station_id,
                    nwp_source=_NWP_SOURCE,
                    cycle_time=_AGG_CYCLE,
                    valid_time=vt,
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=m,
                    value=_agg_precip(m, ts.hour),
                    created_at=_NOW,
                )
            )
            records.append(
                WeatherForecastRecord(
                    id=uuid4(),
                    station_id=station_id,
                    nwp_source=_NWP_SOURCE,
                    cycle_time=_AGG_CYCLE,
                    valid_time=vt,
                    parameter="temperature",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=m,
                    value=_agg_temp(m, ts.hour),
                    created_at=_NOW,
                )
            )
    return records


def _expected_daily() -> tuple[
    dict[tuple[date, int], float], dict[tuple[date, int], float]
]:
    """Independent oracle: plain-Python per-(day, member) precip SUM + temp MEAN."""
    precip_sum: dict[tuple[date, int], float] = defaultdict(float)
    temp_vals: dict[tuple[date, int], list[float]] = defaultdict(list)
    for ts in _AGG_HOURS:
        day = date(ts.year, ts.month, ts.day)
        for m in range(_AGG_MEMBERS):
            precip_sum[(day, m)] += _agg_precip(m, ts.hour)
            temp_vals[(day, m)].append(_agg_temp(m, ts.hour))
    temp_mean = {key: sum(vals) / len(vals) for key, vals in temp_vals.items()}
    return dict(precip_sum), temp_mean


class TestHourlyToDailyNwpAggregation:
    def test_hourly_members_aggregate_to_daily_sum_and_mean(self) -> None:
        # Exercise the raw aggregation directly (unfiltered) so the SUM/MEAN math
        # is asserted without entangling the separate future-filter/cap step
        # (which is covered by TestFutureFilterAndCap). All three UTC-calendar-day
        # buckets are present here (partial first + full + partial last).
        sid = StationId(uuid4())
        points = _aggregate_nwp_records_to_time_step(
            _hourly_ensemble_records(sid), timedelta(days=1)
        )

        # Three daily buckets on UTC midnight, all 21 members, both parameters.
        buckets = sorted({p.valid_time for p in points})
        assert buckets == [
            ensure_utc(datetime(2026, 1, 9, tzinfo=UTC)),
            ensure_utc(datetime(2026, 1, 10, tzinfo=UTC)),
            ensure_utc(datetime(2026, 1, 11, tzinfo=UTC)),
        ]
        assert {p.member_id for p in points} == set(range(_AGG_MEMBERS))
        assert {p.parameter for p in points} == {"precipitation", "temperature"}

        # Known-answer: precip = per-day SUM, temperature = per-day MEAN, per member.
        precip_sum, temp_mean = _expected_daily()
        by_key: dict[tuple[date, int | None, str], float] = {}
        for p in points:
            vt = p.valid_time
            key = (date(vt.year, vt.month, vt.day), p.member_id, p.parameter)
            by_key[key] = p.value

        for (day, m), expected in precip_sum.items():
            assert by_key[(day, m, "precipitation")] == pytest.approx(expected)
        for (day, m), expected in temp_mean.items():
            assert by_key[(day, m, "temperature")] == pytest.approx(expected)


class TestFutureFilterAndCap:
    """Fix 2: a non-midnight cycle backdates the UTC-midnight issue-day bucket to
    ``<= issue_time``; ``_filter_and_cap_daily_records`` drops those and caps to
    ``forecast_horizon_steps``, applied identically across every member."""

    def _daily_points(self) -> list[_AggregatedNwpPoint]:
        sid = StationId(uuid4())
        return _aggregate_nwp_records_to_time_step(
            _hourly_ensemble_records(sid), timedelta(days=1)
        )

    def test_drops_backdated_buckets_and_caps_to_horizon(self) -> None:
        # Non-midnight cycle: issue_time inside the 2026-01-10 UTC day. The
        # backdated 01-09 and 01-10 midnight buckets are <= issue_time and drop;
        # only future buckets survive. Horizon of 1 keeps exactly one.
        issue_time = ensure_utc(datetime(2026, 1, 10, 6, tzinfo=UTC))
        kept = _filter_and_cap_daily_records(
            self._daily_points(), issue_time=issue_time, forecast_horizon_steps=1
        )

        kept_times = sorted({p.valid_time for p in kept})
        assert kept_times == [ensure_utc(datetime(2026, 1, 11, tzinfo=UTC))]
        # No bucket at or before issue_time survives.
        assert all(p.valid_time > issue_time for p in kept)
        # The SAME bucket set is retained for every ensemble member.
        assert {p.member_id for p in kept} == set(range(_AGG_MEMBERS))

    def test_cap_keeps_earliest_n_future_buckets_all_members(self) -> None:
        # issue_time before all buckets => every bucket is "future"; horizon caps
        # to the earliest N. Two future buckets requested -> the first two days.
        issue_time = ensure_utc(datetime(2026, 1, 8, 12, tzinfo=UTC))
        kept = _filter_and_cap_daily_records(
            self._daily_points(), issue_time=issue_time, forecast_horizon_steps=2
        )

        kept_times = sorted({p.valid_time for p in kept})
        assert kept_times == [
            ensure_utc(datetime(2026, 1, 9, tzinfo=UTC)),
            ensure_utc(datetime(2026, 1, 10, tzinfo=UTC)),
        ]
        assert {p.member_id for p in kept} == set(range(_AGG_MEMBERS))


def _assemble_short(
    sid: StationId,
    stores: tuple,
    requirements_override: ModelDataRequirements | None = None,
):
    """Invoke assemble_station_operational_inputs with the shared fixture stores."""
    station_store, basin_store, obs_store, nwp_store, state_store, reanalysis = stores
    return assemble_station_operational_inputs(
        station_id=sid,
        model=_make_model(),
        model_id=_MODEL_ID,
        issue_time=_ISSUE,
        cycle_time=_CYCLE,
        nwp_source=_NWP_SOURCE,
        forcing_source=reanalysis,
        weather_forecast_store=nwp_store,
        obs_store=obs_store,
        station_store=station_store,
        basin_store=basin_store,
        model_state_store=state_store,
        clock=_clock,
        forecast_horizon_steps=5,
        time_step=timedelta(hours=1),
        requirements_override=requirements_override,
    )


def _reqs(targets: set[str], lookback: int = 10) -> ModelDataRequirements:
    """A minimal requirements override with NO dynamic features (isolates the
    short-lookback path from NWP / reanalysis handling)."""
    return ModelDataRequirements(
        target_parameters=frozenset(targets),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=1)}),
        lookback_steps=lookback,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
    )


def _short_events(logs: list[dict]) -> list[dict]:
    return [e for e in logs if e.get("event") == "operational_inputs.short_lookback"]


class TestShortLookbackWarning:
    """Plan 097: warn at input-assembly when the delivered per-target lookback is
    shorter than the model's declared ``lookback_steps``."""

    def test_short_lookback_warns_with_per_target_counts(self) -> None:
        sid = StationId(uuid4())
        stores = _make_stores_and_sources(sid, with_obs=False, with_nwp=False)
        obs_store = stores[2]
        # 5 discharge obs, all inside the 10h lookback window [_ISSUE-10h, _ISSUE).
        obs_store.store_observations(
            make_observations(
                n=5,
                station_id=sid,
                parameter="discharge",
                start=ensure_utc(_ISSUE - timedelta(hours=5)),
                interval=timedelta(hours=1),
            )
        )
        with capture_logs() as logs:
            _assemble_short(sid, stores, requirements_override=_reqs({"discharge"}))

        events = _short_events(logs)
        assert len(events) == 1
        ev = events[0]
        assert ev["log_level"] == "warning"
        assert ev["per_target_counts"] == {"discharge": 5}
        assert ev["lookback_needed"] == 10
        assert ev["lookback_got"] == 5
        assert ev["representative_model_id"] == str(_MODEL_ID)

    def test_wholly_absent_target_counts_zero_and_warns(self) -> None:
        sid = StationId(uuid4())
        stores = _make_stores_and_sources(sid, with_obs=False, with_nwp=False)
        obs_store = stores[2]
        # discharge is healthy (10 in-window); water_level is wholly absent (no
        # column) -> its count must be 0 (column-presence guard, no crash).
        obs_store.store_observations(
            make_observations(
                n=10,
                station_id=sid,
                parameter="discharge",
                start=ensure_utc(_ISSUE - timedelta(hours=10)),
                interval=timedelta(hours=1),
            )
        )
        with capture_logs() as logs:
            _assemble_short(
                sid, stores, requirements_override=_reqs({"discharge", "water_level"})
            )

        events = _short_events(logs)
        assert len(events) == 1
        ev = events[0]
        assert ev["per_target_counts"]["discharge"] == 10
        assert ev["per_target_counts"]["water_level"] == 0
        assert ev["lookback_got"] == 0

    def test_healthy_default_fixture_emits_no_warning(self) -> None:
        # Locks the required _make_stores_and_sources fixture fix: with the fix,
        # the default fixture supplies >= lookback_steps in-window obs, so a
        # healthy station must NOT emit short_lookback.
        sid = StationId(uuid4())
        stores = _make_stores_and_sources(sid)
        with capture_logs() as logs:
            _assemble_short(sid, stores)
        assert _short_events(logs) == []

    def test_no_observations_does_not_double_warn(self) -> None:
        sid = StationId(uuid4())
        stores = _make_stores_and_sources(sid, with_obs=False)
        with capture_logs() as logs:
            _assemble_short(sid, stores)
        # wholly-absent obs is owned by no_observations; short_lookback stays silent.
        assert _short_events(logs) == []
        assert any(e.get("event") == "operational_inputs.no_observations" for e in logs)

    def test_sparse_present_target_uses_non_null_count(self) -> None:
        # A PRESENT target with null rows after resample: discharge is healthy
        # (10 non-null); water_level's column EXISTS but has only 5 non-null rows
        # over the same 10-timestamp union window. lookback_got must be the
        # non-null count (5), not the raw column height (10) — locks .drop_nulls().
        sid = StationId(uuid4())
        stores = _make_stores_and_sources(sid, with_obs=False, with_nwp=False)
        obs_store = stores[2]
        # Distinct RNGs so the two batches get distinct observation ids (a shared
        # default seed would collide ids and the fake store would overwrite rows).
        obs_store.store_observations(
            make_observations(
                n=10,
                station_id=sid,
                parameter="discharge",
                start=ensure_utc(_ISSUE - timedelta(hours=10)),
                interval=timedelta(hours=1),
                rng=random.Random(1),
            )
        )
        obs_store.store_observations(
            make_observations(
                n=5,
                station_id=sid,
                parameter="water_level",
                start=ensure_utc(_ISSUE - timedelta(hours=10)),
                interval=timedelta(hours=1),
                rng=random.Random(2),
            )
        )
        with capture_logs() as logs:
            _assemble_short(
                sid, stores, requirements_override=_reqs({"discharge", "water_level"})
            )

        events = _short_events(logs)
        assert len(events) == 1
        ev = events[0]
        assert ev["per_target_counts"]["discharge"] == 10
        assert ev["per_target_counts"]["water_level"] == 5
        assert ev["lookback_got"] == 5

    def test_empty_target_parameters_emits_no_warning(self) -> None:
        sid = StationId(uuid4())
        stores = _make_stores_and_sources(sid, with_obs=False, with_nwp=False)
        with capture_logs() as logs:
            _assemble_short(sid, stores, requirements_override=_reqs(set()))
        # Empty target_parameters fetches NO observations, so latest_obs_ts is None
        # and short_lookback stays silent; the reqs.target_parameters guard also
        # defensively prevents a min()-of-empty crash on that path.
        assert _short_events(logs) == []
