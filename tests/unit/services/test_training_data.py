from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl

from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
    resample_to_time_step,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AggregationMethod,
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ObservationId, StationGroupId, StationId
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.station import StationGroup, StationWeatherSource
from tests.conftest import (
    make_observations,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeObservationStore,
    FakeStationStore,
)

_START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2020, 6, 1, tzinfo=UTC))
_STEP = timedelta(hours=1)


def _sid() -> StationId:
    return StationId(uuid4())


def _gid() -> StationGroupId:
    return StationGroupId(uuid4())


def _make_forcing(station_id: StationId, n: int = 5) -> list:
    records = []
    for i in range(n):
        ts = ensure_utc(datetime.fromtimestamp(_START.timestamp() + i * 3600, tz=UTC))
        records.append(
            make_raw_historical_forcing(
                station_id=station_id,
                parameter="precipitation",
                valid_time=ts,
                value=float(i),
            )
        )
        records.append(
            make_raw_historical_forcing(
                station_id=station_id,
                parameter="temperature",
                valid_time=ts,
                value=float(10 + i),
            )
        )
    return records


def _weather_source(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
    )


def _seed_station(
    station_id: StationId,
    station_store: FakeStationStore,
    obs_store: FakeObservationStore,
    forcing_list: list,
    *,
    with_obs: bool = True,
    with_forcing: bool = True,
) -> None:
    station_store.store_station(make_station_config(station_id=station_id))
    station_store.store_weather_source(_weather_source(station_id))
    if with_obs:
        obs = make_observations(
            n=10, station_id=station_id, start=_START, rng=random.Random(uuid4().int)
        )
        obs_store.store_observations(obs)
    if with_forcing:
        forcing_list.extend(_make_forcing(station_id))


class TestAssembleStationTrainingDataHappyPath:
    def test_forcing_columns_present(self) -> None:
        model = FakeStationForecastModel()
        station_id = _sid()
        forcing_records: list = []

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        _seed_station(station_id, station_store, obs_store, forcing_records)

        result = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert "precipitation" in result.past_dynamic.columns
        assert "temperature" in result.past_dynamic.columns

    def test_observations_count(self) -> None:
        model = FakeStationForecastModel()
        station_id = _sid()
        forcing_records: list = []

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        _seed_station(station_id, station_store, obs_store, forcing_records)

        result = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert result.past_targets.height == 10
        assert result.time_step == _STEP
        assert result.val_start is None


class TestAssembleStationTrainingDataNone:
    def test_returns_none_no_observations(self) -> None:
        model = FakeStationForecastModel()
        station_id = _sid()
        forcing_records: list = []

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        _seed_station(
            station_id, station_store, obs_store, forcing_records, with_obs=False
        )

        result = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is None

    def test_returns_none_missing_features(self) -> None:
        model = FakeStationForecastModel()
        station_id = _sid()

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        station_store.store_weather_source(_weather_source(station_id))
        obs = make_observations(n=10, station_id=station_id, start=_START)
        obs_store.store_observations(obs)

        # Only precipitation — temperature missing
        partial_forcing = [
            make_raw_historical_forcing(
                station_id=station_id,
                parameter="precipitation",
                valid_time=ensure_utc(
                    datetime.fromtimestamp(_START.timestamp() + i * 3600, tz=UTC)
                ),
                value=float(i),
            )
            for i in range(5)
        ]

        result = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(partial_forcing),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is None

    def test_returns_none_qc_failed_obs_only(self) -> None:
        model = FakeStationForecastModel()
        station_id = _sid()
        forcing_records: list = []

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        station_store.store_weather_source(_weather_source(station_id))
        forcing_records.extend(_make_forcing(station_id))

        failed_obs = [
            Observation(
                id=ObservationId(uuid4()),
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(_START.timestamp() + i * 3600, tz=UTC)
                ),
                parameter="discharge",
                value=None,
                source=ObservationSource.MEASURED,
                rating_curve_id=None,
                rating_curve_correction_version=None,
                qc_status=QcStatus.MISSING,
                qc_flags=[],
                qc_rule_version=None,
                created_at=_START,
            )
            for i in range(5)
        ]
        obs_store.store_observations(failed_obs)

        result = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is None


class TestAssembleStationTrainingDataNoDynamicFeatures:
    def test_assemble_station_training_data_no_dynamic_features(self) -> None:
        """Model with empty past_dynamic_features skips forcing fetch."""
        model = FakeStationForecastModel()  # noqa: F841
        # Override data_requirements with empty past_dynamic_features
        from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
        from sapphire_flow.types.model import ModelDataRequirements

        class _AutoregressiveModel(FakeStationForecastModel):
            artifact_scope = ArtifactScope.STATION
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset(),
                static_features=frozenset(),
                supported_time_steps=frozenset(
                    {timedelta(hours=1), timedelta(hours=24)}
                ),
                lookback_steps=720,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )

        station_id = _sid()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        # Seed station with obs but no forcing registered; no weather source needed
        station_store.store_station(make_station_config(station_id=station_id))
        obs = make_observations(
            n=10, station_id=station_id, start=_START, rng=random.Random(42)
        )
        obs_store.store_observations(obs)

        fake_source = FakeWeatherReanalysisSource()  # empty, never called

        result = assemble_station_training_data(
            station_id=station_id,
            model=_AutoregressiveModel(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=fake_source,
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert set(result.past_dynamic.columns) <= {"timestamp"}
        assert fake_source.fetch_reanalysis_call_count == 0


_DAILY = timedelta(days=1)


def _daily_ts(i: int) -> object:
    return ensure_utc(datetime(2020, 1, 1, tzinfo=UTC) + i * _DAILY)


class TestAssembleStationTrainingDataFutureDynamicDelivery:
    def test_future_known_forcing_delivered_into_future_dynamic(self) -> None:
        """M2 (Fix #1): a model that declares future_dynamic_features (precip/temp)
        must have that forcing fetched and delivered into ``future_dynamic`` —
        NOT cleared. Discharge still comes from observations → ``past_targets``.
        """
        from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
        from sapphire_flow.types.model import ModelDataRequirements

        class _FutureForcingModel(FakeStationForecastModel):
            artifact_scope = ArtifactScope.STATION
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                # discharge is target history, not forcing → past_dynamic empty.
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset({"precipitation", "temperature"}),
                static_features=frozenset(),
                supported_time_steps=frozenset({_DAILY}),
                lookback_steps=7,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )

        station_id = _sid()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        station_store.store_weather_source(_weather_source(station_id))

        # Discharge observations at daily timestamps → past_targets.
        obs = make_observations(
            n=5, station_id=station_id, start=_daily_ts(0), interval=_DAILY
        )
        obs_store.store_observations(obs)

        # Known-answer forcing: specific precip/temp at the same daily timestamps.
        precip_vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        temp_vals = [10.0, 11.0, 12.0, 13.0, 14.0]
        forcing_records: list = []
        for i in range(5):
            forcing_records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter="precipitation",
                    valid_time=_daily_ts(i),
                    value=precip_vals[i],
                )
            )
            forcing_records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter="temperature",
                    valid_time=_daily_ts(i),
                    value=temp_vals[i],
                )
            )

        result = assemble_station_training_data(
            station_id=station_id,
            model=_FutureForcingModel(),
            period_start=_daily_ts(0),
            period_end=_daily_ts(30),
            time_step=_DAILY,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None

        # Discharge target history is unchanged: sourced from observations.
        assert "discharge" in result.past_targets.columns
        assert result.past_targets.height == 5

        # future_known forcing IS delivered into future_dynamic (not cleared).
        future = result.future_dynamic.sort("timestamp")
        assert not future.is_empty()
        assert "precipitation" in future.columns
        assert "temperature" in future.columns
        assert future["precipitation"].to_list() == precip_vals
        assert future["temperature"].to_list() == temp_vals

        # future_dynamic timestamps align to the past_targets timestamps.
        past_ts = result.past_targets.sort("timestamp")["timestamp"].to_list()
        assert future["timestamp"].to_list() == past_ts

    def test_forcing_source_never_asked_for_target(self) -> None:
        """The target (discharge) comes from observations → ``past_targets`` and
        must NEVER be requested from the forcing/reanalysis source, even though its
        history counts toward the model lookback.

        Driven through the REAL adapter-wrapped ``NwpRegression`` (discharge is a
        target AND its own past_known history, lookback == horizon). Under the old
        projection rule discharge leaked into ``past_dynamic_features`` (the forcing
        channel) → the assembler would request it from the forcing source → this
        test is RED. With the fix, discharge is excluded and only precip/temp are
        fetched.
        """
        from sapphire_flow.adapters import forecast_interface as fi_boundary
        from sapphire_flow.models.nwp_regression import NwpRegression

        adapter = fi_boundary.adapt_if_fi(NwpRegression())
        assert isinstance(adapter, fi_boundary.ForecastInterfaceAdapter)

        class _RaiseOnTargetForcing(FakeWeatherReanalysisSource):
            def fetch_reanalysis(
                self,
                station_configs: list[StationWeatherSource],
                start: object,
                end: object,
                parameters: list[str],
            ) -> list:
                if "discharge" in parameters:
                    raise AssertionError(
                        "forcing source asked for the target 'discharge'; "
                        "target history must be sourced from observations"
                    )
                return super().fetch_reanalysis(station_configs, start, end, parameters)

        station_id = _sid()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        station_store.store_weather_source(_weather_source(station_id))

        obs = make_observations(
            n=5, station_id=station_id, start=_daily_ts(0), interval=_DAILY
        )
        obs_store.store_observations(obs)

        forcing_records: list = []
        for i in range(5):
            forcing_records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter="precipitation",
                    valid_time=_daily_ts(i),
                    value=float(i),
                )
            )
            forcing_records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter="temperature",
                    valid_time=_daily_ts(i),
                    value=float(10 + i),
                )
            )

        source = _RaiseOnTargetForcing(forcing_records)
        result = assemble_station_training_data(
            station_id=station_id,
            model=adapter,
            period_start=_daily_ts(0),
            period_end=_daily_ts(30),
            time_step=_DAILY,
            forcing_source=source,
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert "discharge" in result.past_targets.columns
        # Only precip/temp were fetched; discharge never crossed the forcing edge.
        assert set(result.future_dynamic.columns) >= {"precipitation", "temperature"}
        assert "discharge" not in result.future_dynamic.columns
        assert "discharge" not in result.past_dynamic.columns


class TestAssembleGroupTrainingData:
    def test_two_stations_both_with_data(self) -> None:
        sid1 = _sid()
        sid2 = _sid()
        gid = _gid()

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []

        _seed_station(sid1, station_store, obs_store, forcing_records)
        _seed_station(sid2, station_store, obs_store, forcing_records)

        group = StationGroup(
            id=gid,
            name="test-group",
            station_ids=frozenset({sid1, sid2}),
            created_at=_START,
        )

        result = assemble_group_training_data(
            group=group,
            model=FakeGroupForecastModel(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert result.group_id == gid
        assert len(result.station_ids) == 2
        assert sid1 in result.station_ids
        assert sid2 in result.station_ids

    def test_partial_data_one_station(self) -> None:
        sid1 = _sid()
        sid2 = _sid()
        gid = _gid()

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []

        _seed_station(sid1, station_store, obs_store, forcing_records)
        # sid2 has no observations, no forcing
        _seed_station(
            sid2,
            station_store,
            obs_store,
            forcing_records,
            with_obs=False,
            with_forcing=False,
        )

        group = StationGroup(
            id=gid,
            name="partial-group",
            station_ids=frozenset({sid1, sid2}),
            created_at=_START,
        )

        result = assemble_group_training_data(
            group=group,
            model=FakeGroupForecastModel(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert len(result.station_ids) == 1
        assert sid1 in result.station_ids
        assert sid2 not in result.station_ids

    def test_all_stations_missing_returns_none(self) -> None:
        sid1 = _sid()
        sid2 = _sid()
        gid = _gid()

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []

        for sid in (sid1, sid2):
            _seed_station(
                sid,
                station_store,
                obs_store,
                forcing_records,
                with_obs=False,
                with_forcing=False,
            )

        group = StationGroup(
            id=gid,
            name="empty-group",
            station_ids=frozenset({sid1, sid2}),
            created_at=_START,
        )

        result = assemble_group_training_data(
            group=group,
            model=FakeGroupForecastModel(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is None


# ---------------------------------------------------------------------------
# Unit tests for resample_to_time_step
# ---------------------------------------------------------------------------

_BASE = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))


def _hourly_discharge(n_hours: int, value: float = 2.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [
                ensure_utc(datetime.fromtimestamp(_BASE.timestamp() + i * 3600, tz=UTC))
                for i in range(n_hours)
            ],
            "discharge": [value] * n_hours,
        }
    )


def _hourly_precipitation(n_hours: int, value: float = 1.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [
                ensure_utc(datetime.fromtimestamp(_BASE.timestamp() + i * 3600, tz=UTC))
                for i in range(n_hours)
            ],
            "precipitation": [value] * n_hours,
        }
    )


class TestResampleToTimeStep:
    def test_hourly_to_daily_mean(self) -> None:
        df = _hourly_discharge(48, value=3.0)
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 2
        assert abs(result["discharge"].mean() - 3.0) < 1e-9  # type: ignore[operator]

    def test_hourly_to_daily_sum(self) -> None:
        df = _hourly_precipitation(24, value=1.0)
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 1
        assert abs(result["precipitation"][0] - 24.0) < 1e-9

    def test_already_daily_is_idempotent(self) -> None:
        daily_timestamps = [
            ensure_utc(datetime.fromtimestamp(_BASE.timestamp() + i * 86400, tz=UTC))
            for i in range(5)
        ]
        df = pl.DataFrame({"timestamp": daily_timestamps, "discharge": [1.0] * 5})
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 5
        assert result["discharge"].to_list() == [1.0] * 5

    def test_mixed_parameters_mean_and_sum(self) -> None:
        n = 24
        base_ts = [
            ensure_utc(datetime.fromtimestamp(_BASE.timestamp() + i * 3600, tz=UTC))
            for i in range(n)
        ]
        df = pl.DataFrame(
            {
                "timestamp": base_ts,
                "discharge": [2.0] * n,
                "precipitation": [1.0] * n,
            }
        )
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 1
        assert abs(result["discharge"][0] - 2.0) < 1e-9  # mean
        assert abs(result["precipitation"][0] - 24.0) < 1e-9  # sum

    def test_none_aggregation_methods_uses_fallback(self) -> None:
        df = _hourly_discharge(24, value=5.0)
        result = resample_to_time_step(df, timedelta(days=1), aggregation_methods=None)
        assert result.height == 1
        assert abs(result["discharge"][0] - 5.0) < 1e-9

    def test_explicit_aggregation_methods_override(self) -> None:
        df = _hourly_discharge(24, value=4.0)
        methods = {"discharge": AggregationMethod.SUM}
        result = resample_to_time_step(
            df, timedelta(days=1), aggregation_methods=methods
        )
        assert result.height == 1
        assert abs(result["discharge"][0] - 96.0) < 1e-9  # 24 * 4.0
