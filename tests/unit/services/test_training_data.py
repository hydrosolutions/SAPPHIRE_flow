from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest
from structlog.testing import capture_logs

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.training_data import (
    aligned_lookback_bounds,
    assemble_group_training_data,
    assemble_station_training_data,
    floor_to_time_step,
    resample_to_time_step,
    validate_time_step_cadence,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AggregationMethod,
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import BasinId, ObservationId, StationGroupId, StationId
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
        role=WeatherSourceRole.REANALYSIS,
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


def _required_static_model() -> object:
    """A station-scoped model that REQUIRES static features (D-UP gate)."""
    from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
    from sapphire_flow.types.model import ModelDataRequirements

    class _StaticRequiredModel(FakeStationForecastModel):
        artifact_scope = ArtifactScope.STATION
        data_requirements = ModelDataRequirements(
            target_parameters=frozenset({"discharge"}),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset(),
            static_features=frozenset({"elevation_mean"}),
            supported_time_steps=frozenset({timedelta(hours=1), timedelta(hours=24)}),
            lookback_steps=720,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.POINT,
        )

    return _StaticRequiredModel()


def _basin(basin_id: BasinId, *, attributes: dict[str, object] | None) -> Basin:
    return Basin(
        id=basin_id,
        code="B-001",
        name="Basin B-001",
        geometry=None,
        area_km2=100.0,
        attributes=attributes,
        band_geometries=None,
        created_at=_START,
        network="bafu",
    )


class TestAssembleStationTrainingDataStaticFeatureGate:
    """Plan 120 Task 2D D-UP prerequisite: a required-static model must fail
    loud UPSTREAM (return None) whenever the basin row is dangling or its
    attributes are absent/empty — not only when basin_id itself is None."""

    def test_dangling_basin_id_returns_none(self) -> None:
        station_id = _sid()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []
        _seed_station(station_id, station_store, obs_store, forcing_records)
        # basin_id set on the station config, but never stored in basin_store
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=BasinId(uuid4()))
        )

        result = assemble_station_training_data(
            station_id=station_id,
            model=_required_static_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),  # empty — basin_id is dangling
            station_store=station_store,
        )

        assert result is None

    def test_empty_basin_attributes_returns_none(self) -> None:
        station_id = _sid()
        basin_id = BasinId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []
        _seed_station(station_id, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin(basin_id, attributes=None))

        result = assemble_station_training_data(
            station_id=station_id,
            model=_required_static_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is None

    def test_null_basin_id_still_returns_none(self) -> None:
        """Pre-existing branch — must keep working after the gate widening."""
        station_id = _sid()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []
        _seed_station(station_id, station_store, obs_store, forcing_records)

        result = assemble_station_training_data(
            station_id=station_id,
            model=_required_static_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is None

    def test_populated_basin_attributes_succeeds(self) -> None:
        station_id = _sid()
        basin_id = BasinId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []
        _seed_station(station_id, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin(basin_id, attributes={"elevation_mean": 1200.0}))

        result = assemble_station_training_data(
            station_id=station_id,
            model=_required_static_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is not None
        assert result.static is not None

    def test_null_valued_required_static_attribute_returns_none(self) -> None:
        """Codex review (Plan 120 fixer round, major): a required static
        feature whose KEY is present but whose VALUE is `None` must be
        treated as missing -- the gate previously only checked key
        presence, so `{"elevation_mean": None}` passed and let a
        required-static model train on a null static value undetected."""
        station_id = _sid()
        basin_id = BasinId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []
        _seed_station(station_id, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin(basin_id, attributes={"elevation_mean": None}))

        result = assemble_station_training_data(
            station_id=station_id,
            model=_required_static_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is None


class TestAssembleGroupTrainingDataStaticFeatureGate:
    def test_group_excludes_member_with_dangling_basin(self) -> None:
        """D-UP applies through the group path too: a group member whose
        basin is dangling is excluded upstream (N-1), not silently trained
        with static_attributes=None."""
        sid_ok = _sid()
        sid_dangling = _sid()
        basin_id_ok = BasinId(uuid4())
        gid = _gid()

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []

        _seed_station(sid_ok, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=sid_ok, basin_id=basin_id_ok)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(
            _basin(basin_id_ok, attributes={"elevation_mean": 500.0})
        )

        _seed_station(sid_dangling, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=sid_dangling, basin_id=BasinId(uuid4()))
        )

        group = StationGroup(
            id=gid,
            name="static-gate-group",
            station_ids=frozenset({sid_ok, sid_dangling}),
            created_at=_START,
        )

        from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
        from sapphire_flow.types.model import ModelDataRequirements

        class _StaticRequiredGroupModel(FakeGroupForecastModel):
            artifact_scope = ArtifactScope.GROUP
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset(),
                static_features=frozenset({"elevation_mean"}),
                supported_time_steps=frozenset(
                    {timedelta(hours=1), timedelta(hours=24)}
                ),
                lookback_steps=720,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )

        result = assemble_group_training_data(
            group=group,
            model=_StaticRequiredGroupModel(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is not None
        assert len(result.station_ids) == 1
        assert sid_ok in result.station_ids
        assert sid_dangling not in result.station_ids

    def test_group_excludes_member_with_null_valued_static_attribute(self) -> None:
        """Same as above but the excluded member has a basin row whose
        required static key is present with value `None`, not absent."""
        sid_ok = _sid()
        sid_null = _sid()
        basin_id_ok = BasinId(uuid4())
        basin_id_null = BasinId(uuid4())
        gid = _gid()

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_records: list = []

        _seed_station(sid_ok, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=sid_ok, basin_id=basin_id_ok)
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(
            _basin(basin_id_ok, attributes={"elevation_mean": 500.0})
        )

        _seed_station(sid_null, station_store, obs_store, forcing_records)
        station_store.store_station(
            make_station_config(station_id=sid_null, basin_id=basin_id_null)
        )
        basin_store.store_basin(
            _basin(basin_id_null, attributes={"elevation_mean": None})
        )

        group = StationGroup(
            id=gid,
            name="static-gate-group-null",
            station_ids=frozenset({sid_ok, sid_null}),
            created_at=_START,
        )

        from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
        from sapphire_flow.types.model import ModelDataRequirements

        class _StaticRequiredGroupModel(FakeGroupForecastModel):
            artifact_scope = ArtifactScope.GROUP
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset(),
                future_dynamic_features=frozenset(),
                static_features=frozenset({"elevation_mean"}),
                supported_time_steps=frozenset(
                    {timedelta(hours=1), timedelta(hours=24)}
                ),
                lookback_steps=720,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )

        result = assemble_group_training_data(
            group=group,
            model=_StaticRequiredGroupModel(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource(forcing_records),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is not None
        assert len(result.station_ids) == 1
        assert sid_ok in result.station_ids
        assert sid_null not in result.station_ids


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

    def test_buckets_are_utc_calendar_aligned_never_phase_aligned(self) -> None:
        """Plan 228 D4: a previous fixer round's ``anchor`` parameter
        phase-aligned buckets to a caller's own timestamp; D4 retracts
        that — every path aggregates onto UTC-calendar buckets, full stop.
        3 days of 10-minute discharge starting at 06:00 UTC (never a
        midnight boundary) must still bucket to UTC midnight."""
        start = ensure_utc(datetime(2024, 1, 1, 6, tzinfo=UTC))
        n = 3 * 24 * 6
        df = pl.DataFrame(
            {
                "timestamp": [
                    ensure_utc(
                        datetime.fromtimestamp(start.timestamp() + i * 600, tz=UTC)
                    )
                    for i in range(n)
                ],
                "discharge": [5.0] * n,
            }
        )

        result = resample_to_time_step(df, timedelta(days=1))

        # Every bucket boundary must fall at UTC midnight, never at 06:00.
        for ts in result["timestamp"]:
            assert ts.hour == 0 and ts.minute == 0 and ts.second == 0, (
                f"bucket at {ts} is not UTC-calendar aligned"
            )


class TestResampleAlreadyStepSizedDataStillCalendarAligns:
    """Plan 228 review fixer round (major): the fast path used to trigger on
    cadence match ALONE — already-daily rows stamped off the UTC-calendar
    grid (e.g. every row at 06:00, from a non-midnight operational cycle)
    were returned byte-for-byte unchanged, never rebucketed. Those rows then
    pass ``validate_time_step_cadence`` (which only checks GAPS, not phase)
    and can join 06:00 forecasts — exactly the phase-aligned behavior D4
    forbids. The existing UTC-calendar test
    (``test_buckets_are_utc_calendar_aligned_never_phase_aligned``) uses
    finer 10-minute input, so it never enters this fast path at all.
    """

    def test_already_daily_cadence_at_0600_is_still_rebucketed_to_midnight(
        self,
    ) -> None:
        start = ensure_utc(datetime(2024, 1, 1, 6, tzinfo=UTC))
        n = 5
        df = pl.DataFrame(
            {
                "timestamp": [
                    ensure_utc(
                        datetime.fromtimestamp(start.timestamp() + i * 86400, tz=UTC)
                    )
                    for i in range(n)
                ],
                "discharge": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        )

        result = resample_to_time_step(df, timedelta(days=1))

        for ts in result["timestamp"]:
            assert ts.hour == 0 and ts.minute == 0 and ts.second == 0, (
                f"already-daily-cadence bucket at {ts} is still phase-aligned "
                "to 06:00, not the UTC-calendar grid"
            )

    def test_single_misaligned_row_is_floored_to_the_calendar_grid(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [ensure_utc(datetime(2024, 1, 1, 6, tzinfo=UTC))],
                "discharge": [42.0],
            }
        )

        result = resample_to_time_step(df, timedelta(days=1))

        assert result.height == 1
        ts = result["timestamp"][0]
        assert ts == ensure_utc(datetime(2024, 1, 1, tzinfo=UTC)), (
            f"single-row frame at {ts} was returned unchanged instead of "
            "floored onto the UTC-calendar grid"
        )
        assert abs(result["discharge"][0] - 42.0) < 1e-9


class TestValidateTimeStepCadence:
    """Plan 228 review fixer round (major): direct unit coverage for
    ``validate_time_step_cadence``, wired as a hard `ConfigurationError`-
    raising check into the live operational forecast cycle
    (`operational_inputs.py`, `track_assembly.py`) and hindcast
    (`hindcast.py`), with previously zero direct tests anywhere."""

    def test_raises_on_a_genuinely_mismatched_cadence(self) -> None:
        # Raw ~10-minute-cadence data against a declared daily time_step —
        # the exact P1 shape (144x shortfall).
        df = pl.DataFrame(
            {
                "timestamp": [
                    ensure_utc(
                        datetime.fromtimestamp(_BASE.timestamp() + i * 600, tz=UTC)
                    )
                    for i in range(20)
                ],
                "discharge": [1.0] * 20,
            }
        )
        with pytest.raises(ConfigurationError, match="does not match"):
            validate_time_step_cadence(df, timedelta(days=1), context="test")

    def test_does_not_raise_on_a_clean_uniform_cadence(self) -> None:
        df = _hourly_discharge(48, value=1.0)
        # Should not raise: cadence matches declared time_step exactly.
        validate_time_step_cadence(df, timedelta(hours=1), context="test")

    def test_raises_on_an_isolated_missing_bucket(self) -> None:
        # Plan 228 review fixer round (major): days 1,2,3,5,6 — gaps
        # 1d,1d,2d,1d. The median gap is still 1d (3 of 4 gaps match), so a
        # median-only check passes this straight through; every adjacent
        # gap must be checked to catch the isolated 2-day gap on day 4.
        days = [1, 2, 3, 5, 6]
        df = pl.DataFrame(
            {
                "timestamp": [
                    ensure_utc(
                        datetime.fromtimestamp(
                            _BASE.timestamp() + (d - 1) * 86400, tz=UTC
                        )
                    )
                    for d in days
                ],
                "discharge": [1.0] * len(days),
            }
        )
        with pytest.raises(ConfigurationError, match="does not match"):
            validate_time_step_cadence(df, timedelta(days=1), context="test")

    def test_does_not_raise_on_fewer_than_two_rows(self) -> None:
        df = pl.DataFrame({"timestamp": [_BASE], "discharge": [1.0]})
        validate_time_step_cadence(df, timedelta(days=1), context="test")


class TestResampleSnowAggregationFallback:
    """Plan 145 D2: swe/snow_depth (states) MEAN, snowmelt (flux) SUMs — via the
    ``_V0_AGGREGATION_FALLBACK`` default (no explicit aggregation_methods)."""

    def _hourly_frame(self, n: int, **columns: float) -> pl.DataFrame:
        base_ts = [
            ensure_utc(datetime.fromtimestamp(_BASE.timestamp() + i * 3600, tz=UTC))
            for i in range(n)
        ]
        data: dict[str, object] = {"timestamp": base_ts}
        for col, value in columns.items():
            data[col] = [value] * n
        return pl.DataFrame(data)

    def test_snowmelt_sums_hourly_to_daily(self) -> None:
        df = self._hourly_frame(24, snowmelt=2.0)
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 1
        assert abs(result["snowmelt"][0] - 48.0) < 1e-9  # 24 * 2.0, not the mean 2.0

    def test_swe_means_hourly_to_daily(self) -> None:
        df = self._hourly_frame(24, swe=7.5)
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 1
        assert abs(result["swe"][0] - 7.5) < 1e-9  # mean, not the sum 180.0

    def test_snow_depth_means_hourly_to_daily(self) -> None:
        df = self._hourly_frame(24, snow_depth=3.0)
        result = resample_to_time_step(df, timedelta(days=1))
        assert result.height == 1
        assert abs(result["snow_depth"][0] - 3.0) < 1e-9  # mean, not the sum 72.0

    def test_no_unknown_parameter_warning_for_snow_columns(self) -> None:
        df = self._hourly_frame(24, swe=1.0, snow_depth=2.0, snowmelt=3.0)
        with capture_logs() as logs:
            resample_to_time_step(df, timedelta(days=1))
        unknown_events = [
            e
            for e in logs
            if e.get("event") == "resample_to_time_step.unknown_parameter"
        ]
        assert unknown_events == []


class TestSnowReachesPastDynamicViaHybridSource:
    """Plan 146 D4/3a: the SAME stored ``recap_snow_reanalysis`` series reaches
    ``past_dynamic`` through the real ``default_hybrid_forcing_source`` — not
    a fake reanalysis source — proving the read-side routing wiring end to
    end for the training consumer."""

    def test_swe_column_present_with_stored_value(self) -> None:
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            default_hybrid_forcing_source,
        )
        from sapphire_flow.types.enums import ArtifactScope
        from sapphire_flow.types.model import ModelDataRequirements
        from tests.fakes.fake_stores import FakeHistoricalForcingStore

        class _SnowFedModel(FakeStationForecastModel):
            artifact_scope = ArtifactScope.STATION
            data_requirements = ModelDataRequirements(
                target_parameters=frozenset({"discharge"}),
                past_dynamic_features=frozenset({"swe"}),
                future_dynamic_features=frozenset(),
                static_features=frozenset(),
                supported_time_steps=frozenset({_DAILY}),
                lookback_steps=7,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            )

        station_id = _sid()
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        station_store.store_station(make_station_config(station_id=station_id))
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=station_id,
                nwp_source="era5_land",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        obs = make_observations(
            n=5, station_id=station_id, start=_daily_ts(0), interval=_DAILY
        )
        obs_store.store_observations(obs)

        forcing_store = FakeHistoricalForcingStore()
        forcing_store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station_id,
                    source="recap_snow_reanalysis",
                    parameter="swe",
                    valid_time=_daily_ts(i),
                    value=100.0 + i,
                )
                for i in range(5)
            ]
        )

        result = assemble_station_training_data(
            station_id=station_id,
            model=_SnowFedModel(),
            period_start=_daily_ts(0),
            period_end=_daily_ts(30),
            time_step=_DAILY,
            forcing_source=default_hybrid_forcing_source(forcing_store=forcing_store),
            obs_store=obs_store,
            basin_store=FakeBasinStore(),
            station_store=station_store,
        )

        assert result is not None
        assert "swe" in result.past_dynamic.columns
        values = result.past_dynamic.sort("timestamp")["swe"].to_list()
        assert values == [100.0, 101.0, 102.0, 103.0, 104.0]


class TestFloorToTimeStep:
    """Plan 228 D4: the UTC-calendar bucket boundary at or before an
    instant — never phase-aligned to the instant itself."""

    def test_midnight_instant_is_its_own_floor(self) -> None:
        midnight = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
        assert floor_to_time_step(midnight, timedelta(days=1)) == midnight

    def test_non_midnight_instant_floors_to_the_preceding_midnight(self) -> None:
        six_am = ensure_utc(datetime(2026, 1, 10, 6, tzinfo=UTC))
        expected = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
        assert floor_to_time_step(six_am, timedelta(days=1)) == expected

    def test_hourly_step_floors_to_the_hour(self) -> None:
        instant = ensure_utc(datetime(2026, 1, 10, 6, 45, 30, tzinfo=UTC))
        expected = ensure_utc(datetime(2026, 1, 10, 6, tzinfo=UTC))
        assert floor_to_time_step(instant, timedelta(hours=1)) == expected


class TestAlignedLookbackBounds:
    """Plan 228 D4: fetch bounds covering exactly ``lookback_steps``
    COMPLETE UTC-calendar buckets, ending at the boundary at or before the
    given instant — never a naive ``instant - lookback_steps * time_step``,
    which has a partial bucket at both ends whenever ``instant`` is not
    itself a boundary."""

    def test_end_is_the_boundary_at_or_before_instant(self) -> None:
        issue_time = ensure_utc(datetime(2026, 1, 15, 6, tzinfo=UTC))
        _, end = aligned_lookback_bounds(issue_time, 7, timedelta(days=1))
        assert end == ensure_utc(datetime(2026, 1, 15, tzinfo=UTC))
        assert end <= issue_time  # NO-FUTURE-LEAKAGE

    def test_start_is_exactly_lookback_steps_before_the_aligned_end(self) -> None:
        issue_time = ensure_utc(datetime(2026, 1, 15, 6, tzinfo=UTC))
        start, end = aligned_lookback_bounds(issue_time, 7, timedelta(days=1))
        assert end - start == 7 * timedelta(days=1)

    def test_a_midnight_instant_reproduces_the_naive_window(self) -> None:
        # When `instant` IS already a boundary, alignment is a no-op — the
        # aligned window equals the naive `instant - lookback * step` one.
        issue_time = ensure_utc(datetime(2026, 1, 15, tzinfo=UTC))
        start, end = aligned_lookback_bounds(issue_time, 7, timedelta(days=1))
        assert end == issue_time
        assert start == ensure_utc(issue_time - 7 * timedelta(days=1))
