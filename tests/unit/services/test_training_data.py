from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
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
