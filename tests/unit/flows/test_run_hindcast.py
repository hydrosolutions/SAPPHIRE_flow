from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.flows.run_hindcast import run_hindcast_flow
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
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
    FakeHindcastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeObservationStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_STEP = timedelta(hours=24)
_PERIOD_START = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))
_PERIOD_END = ensure_utc(datetime(2022, 1, 4, tzinfo=UTC))  # 3 issue times
_MODEL_ID = ModelId("test_model")


def _utc(year: int, month: int, day: int) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, tzinfo=UTC))


def _fixed_clock() -> UtcDatetime:
    return _utc(2022, 6, 1)


def _make_weather_source(station_id: StationId) -> StationWeatherSource:
    return StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def _seed_data(
    obs_store: FakeObservationStore,
    forcing_source: FakeWeatherReanalysisSource,
    station_id: StationId,
    n_days: int = 400,
) -> None:
    data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
    obs = make_observations(
        n=n_days * 24,
        station_id=station_id,
        start=data_start,
        interval=timedelta(hours=1),
    )
    obs_store.store_observations(obs)
    records = []
    for i in range(n_days * 24):
        ts = ensure_utc(
            datetime.fromtimestamp(data_start.timestamp() + i * 3600, tz=UTC)
        )
        for param in ("precipitation", "temperature"):
            records.append(
                make_raw_historical_forcing(
                    station_id=station_id,
                    parameter=param,
                    valid_time=ts,
                    value=float(i % 20),
                )
            )
    forcing_source.set_records(forcing_source.records() + records)


def _build_station_stores(
    station_id: StationId,
) -> tuple[
    FakeStationStore,
    FakeObservationStore,
    FakeWeatherReanalysisSource,
    FakeHindcastStore,
    FakeBasinStore,
]:
    station_store = FakeStationStore()
    obs_store = FakeObservationStore()
    forcing_source = FakeWeatherReanalysisSource()
    hindcast_store = FakeHindcastStore()
    basin_store = FakeBasinStore()

    station = make_station_config(station_id=station_id)
    station_store.store_station(station)
    station_store.store_weather_source(_make_weather_source(station_id))
    _seed_data(obs_store, forcing_source, station_id)

    return station_store, obs_store, forcing_source, hindcast_store, basin_store


class TestRunHindcastFlowStationPath:
    def test_station_hindcast_stores_results(self) -> None:
        rng = random.Random(0)
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        station_store, obs_store, forcing_source, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )
        model = FakeStationForecastModel()

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=artifact_id,
            station_id=sid,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            model=model,
            artifact=b"fake_artifact",
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, list)
        assert len(results) == 3
        assert all(r.success for r in results)
        assert len(hindcast_store._hindcasts) > 0
        for h in hindcast_store._hindcasts.values():
            assert h.model_id == _MODEL_ID
            assert h.model_artifact_id == artifact_id
            assert h.hindcast_run_id == run_id

    def test_model_resolved_from_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = random.Random(0)
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        station_store, obs_store, forcing_source, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {_MODEL_ID: FakeStationForecastModel()},
        )

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=artifact_id,
            station_id=sid,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            model=None,
            artifact=b"fake_artifact",
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, list)
        assert len(results) == 3

    def test_model_not_in_registry_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())

        station_store, obs_store, forcing_source, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {},
        )

        with pytest.raises(ValueError, match="not found in registry"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                station_id=sid,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=None,
                artifact=b"fake_artifact",
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=hindcast_store,
                station_store=station_store,
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=random.Random(0),
            )

    def test_artifact_resolved_from_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = random.Random(0)
        sid = StationId(uuid4())
        run_id = uuid4()

        station_store, obs_store, forcing_source_fake, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )

        artifact_store = FakeModelArtifactStore()
        aid, _ = artifact_store.store_artifact(
            model_id=_MODEL_ID,
            artifact_bytes=b"fake_artifact",
            training_period_start=_PERIOD_START,
            training_period_end=_PERIOD_END,
            trained_at=_utc(2022, 6, 1),
            station_id=sid,
        )

        forcing_hist_store = FakeHistoricalForcingStore()
        records = []
        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        n_days = 400
        for i in range(n_days * 24):
            ts = ensure_utc(
                datetime.fromtimestamp(data_start.timestamp() + i * 3600, tz=UTC)
            )
            for param in ("precipitation", "temperature"):
                records.append(
                    make_raw_historical_forcing(
                        station_id=sid,
                        parameter=param,
                        valid_time=ts,
                        value=float(i % 20),
                    )
                )
        forcing_hist_store.store_forcing(records)

        stores_dict = {
            "station_store": station_store,
            "group_store": FakeStationGroupStore(),
            "obs_store": obs_store,
            "hindcast_store": hindcast_store,
            "basin_store": basin_store,
            "artifact_store": artifact_store,
            "forcing_store": forcing_hist_store,
        }
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores",
            lambda _url: (None, stores_dict),
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {_MODEL_ID: FakeStationForecastModel()},
        )

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=aid,
            station_id=sid,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, list)
        assert len(results) == 3

    def test_artifact_not_found_in_store_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = StationId(uuid4())
        run_id = uuid4()

        station_store, obs_store, _, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )

        artifact_store = FakeModelArtifactStore()  # empty — no artifact stored
        forcing_hist_store = FakeHistoricalForcingStore()

        stores_dict = {
            "station_store": station_store,
            "group_store": FakeStationGroupStore(),
            "obs_store": obs_store,
            "hindcast_store": hindcast_store,
            "basin_store": basin_store,
            "artifact_store": artifact_store,
            "forcing_store": forcing_hist_store,
        }
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores",
            lambda _url: (None, stores_dict),
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {_MODEL_ID: FakeStationForecastModel()},
        )

        with pytest.raises(ValueError, match="not found in store"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=ArtifactId(uuid4()),
                station_id=sid,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                clock=_fixed_clock,
                rng=random.Random(0),
                hindcast_run_id=run_id,
            )

    def test_artifact_resolution_without_store_resolution_raises(self) -> None:
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())

        station_store, obs_store, forcing_source, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )

        with pytest.raises(ValueError, match="Cannot resolve artifact"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                station_id=sid,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=FakeStationForecastModel(),
                artifact=None,
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=hindcast_store,
                station_store=station_store,
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=random.Random(0),
            )

    def test_explicit_model_not_overridden_by_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = random.Random(0)
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        station_store, obs_store, forcing_source, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: (_ for _ in ()).throw(RuntimeError("should not be called")),
        )

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=artifact_id,
            station_id=sid,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            model=FakeStationForecastModel(),
            artifact=b"fake_artifact",
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, list)


class _CombinedStationGroupStore(FakeStationStore, FakeStationGroupStore):
    """Fake store supporting both station and group operations."""

    def __init__(self) -> None:
        FakeStationStore.__init__(self)
        FakeStationGroupStore.__init__(self)


class TestRunHindcastFlowGroupPath:
    def _build_group_stores(
        self, station_ids: list[StationId]
    ) -> tuple[
        _CombinedStationGroupStore,
        FakeObservationStore,
        FakeWeatherReanalysisSource,
        FakeHindcastStore,
        FakeBasinStore,
        StationGroupId,
    ]:
        group_id = StationGroupId(uuid4())
        group = StationGroup(
            id=group_id,
            name="test-group",
            station_ids=frozenset(station_ids),
            created_at=_utc(2022, 1, 1),
        )

        combined_store = _CombinedStationGroupStore()
        combined_obs_store = FakeObservationStore()
        combined_forcing = FakeWeatherReanalysisSource()
        combined_hindcast_store = FakeHindcastStore()
        combined_basin_store = FakeBasinStore()

        for sid in station_ids:
            station = make_station_config(station_id=sid)
            combined_store.store_station(station)
            combined_store.store_weather_source(_make_weather_source(sid))
            _seed_data(combined_obs_store, combined_forcing, sid)

        combined_store.store_group(group)

        return (
            combined_store,
            combined_obs_store,
            combined_forcing,
            combined_hindcast_store,
            combined_basin_store,
            group_id,
        )

    def test_group_hindcast_produces_per_station_results(self) -> None:
        rng = random.Random(1)
        sid1 = StationId(uuid4())
        sid2 = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        (
            combined_store,
            obs_store,
            forcing_source,
            hindcast_store,
            basin_store,
            group_id,
        ) = self._build_group_stores([sid1, sid2])

        model = FakeGroupForecastModel()

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=artifact_id,
            group_id=group_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            model=model,
            artifact=b"fake_group_artifact",
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=combined_store,
            group_store=combined_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, dict)
        assert sid1 in results
        assert sid2 in results

    def test_group_not_found_raises(self) -> None:
        group_id = StationGroupId(uuid4())
        artifact_id = ArtifactId(uuid4())
        model = FakeGroupForecastModel()

        combined_store = _CombinedStationGroupStore()  # empty — group_id not registered

        with pytest.raises(ValueError, match=f"Group {group_id} not found"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                group_id=group_id,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=model,
                artifact=b"fake_artifact",
                forcing_source=FakeWeatherReanalysisSource(),
                obs_store=FakeObservationStore(),
                hindcast_store=FakeHindcastStore(),
                station_store=combined_store,
                group_store=combined_store,
                basin_store=FakeBasinStore(),
                clock=_fixed_clock,
                rng=random.Random(0),
            )

    def test_group_model_not_in_registry_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = random.Random(1)
        sid1 = StationId(uuid4())
        sid2 = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        (
            combined_store,
            obs_store,
            forcing_source,
            hindcast_store,
            basin_store,
            group_id,
        ) = self._build_group_stores([sid1, sid2])

        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {},
        )

        with pytest.raises(ValueError, match="not found in registry"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                group_id=group_id,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=None,
                artifact=b"fake_group_artifact",
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=hindcast_store,
                station_store=combined_store,
                group_store=combined_store,
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=rng,
                hindcast_run_id=run_id,
            )

    def test_group_artifact_resolution_without_store_resolution_raises(self) -> None:
        rng = random.Random(1)
        sid1 = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())

        (
            combined_store,
            obs_store,
            forcing_source,
            hindcast_store,
            basin_store,
            group_id,
        ) = self._build_group_stores([sid1])

        with pytest.raises(ValueError, match="Cannot resolve artifact"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                group_id=group_id,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=FakeGroupForecastModel(),
                artifact=None,
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=hindcast_store,
                station_store=combined_store,
                group_store=combined_store,
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=rng,
            )

    def test_group_path_self_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rng = random.Random(1)
        sid1 = StationId(uuid4())
        sid2 = StationId(uuid4())
        run_id = uuid4()

        (
            combined_store,
            obs_store,
            forcing_source_fake,
            hindcast_store,
            basin_store,
            group_id,
        ) = self._build_group_stores([sid1, sid2])

        artifact_store = FakeModelArtifactStore()
        aid, _ = artifact_store.store_artifact(
            model_id=_MODEL_ID,
            artifact_bytes=b"fake_group_artifact",
            training_period_start=_PERIOD_START,
            training_period_end=_PERIOD_END,
            trained_at=_utc(2022, 6, 1),
            group_id=group_id,
        )

        forcing_hist_store = FakeHistoricalForcingStore()

        stores_dict = {
            "station_store": combined_store,
            "group_store": combined_store,
            "obs_store": obs_store,
            "hindcast_store": hindcast_store,
            "basin_store": basin_store,
            "artifact_store": artifact_store,
            "forcing_store": forcing_hist_store,
        }
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores",
            lambda _url: (None, stores_dict),
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {_MODEL_ID: FakeGroupForecastModel()},
        )

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=aid,
            group_id=group_id,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            forcing_source=forcing_source_fake,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, dict)


class TestRunHindcastFlowSelfResolution:
    def test_full_self_resolution_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = random.Random(0)
        sid = StationId(uuid4())
        run_id = uuid4()

        station_store, obs_store, forcing_source_fake, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )

        artifact_store = FakeModelArtifactStore()
        aid, _ = artifact_store.store_artifact(
            model_id=_MODEL_ID,
            artifact_bytes=b"fake_artifact",
            training_period_start=_PERIOD_START,
            training_period_end=_PERIOD_END,
            trained_at=_utc(2022, 6, 1),
            station_id=sid,
        )

        forcing_hist_store = FakeHistoricalForcingStore()
        records = []
        data_start = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
        n_days = 400
        for i in range(n_days * 24):
            ts = ensure_utc(
                datetime.fromtimestamp(data_start.timestamp() + i * 3600, tz=UTC)
            )
            for param in ("precipitation", "temperature"):
                records.append(
                    make_raw_historical_forcing(
                        station_id=sid,
                        parameter=param,
                        valid_time=ts,
                        value=float(i % 20),
                    )
                )
        forcing_hist_store.store_forcing(records)

        stores_dict = {
            "station_store": station_store,
            "group_store": FakeStationGroupStore(),
            "obs_store": obs_store,
            "hindcast_store": hindcast_store,
            "basin_store": basin_store,
            "artifact_store": artifact_store,
            "forcing_store": forcing_hist_store,
        }
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores",
            lambda _url: (None, stores_dict),
        )
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {_MODEL_ID: FakeStationForecastModel()},
        )

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=aid,
            station_id=sid,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, list)
        assert len(results) == 3

    def test_self_resolution_skipped_when_stores_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rng = random.Random(0)
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())
        run_id = uuid4()

        station_store, obs_store, forcing_source, hindcast_store, basin_store = (
            _build_station_stores(sid)
        )
        monkeypatch.delenv("DATABASE_URL", raising=False)

        results = run_hindcast_flow.fn(
            model_id=_MODEL_ID,
            artifact_id=artifact_id,
            station_id=sid,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            time_step=_STEP,
            model=FakeStationForecastModel(),
            artifact=b"fake_artifact",
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=_fixed_clock,
            rng=rng,
            hindcast_run_id=run_id,
        )

        assert isinstance(results, list)

    def test_missing_database_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sid = StationId(uuid4())
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(KeyError):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=ArtifactId(uuid4()),
                station_id=sid,
                station_store=None,
            )


class TestRunHindcastFlowValidation:
    def test_neither_station_nor_group_raises(self) -> None:
        artifact_id = ArtifactId(uuid4())
        model = FakeStationForecastModel()

        with pytest.raises(
            ValueError, match="Either station_id or group_id must be provided"
        ):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                model=model,
                artifact=b"fake_artifact",
            )

    def test_partial_stores_raises(self) -> None:
        sid = StationId(uuid4())
        artifact_id = ArtifactId(uuid4())

        station_store = FakeStationStore()
        station = make_station_config(station_id=sid)
        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid))

        with pytest.raises(ValueError, match="Required dependencies are None"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=artifact_id,
                station_id=sid,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=FakeStationForecastModel(),
                artifact=b"fake_artifact",
                forcing_source=FakeWeatherReanalysisSource(),
                obs_store=None,  # missing
                hindcast_store=FakeHindcastStore(),
                station_store=station_store,
                basin_store=FakeBasinStore(),
                clock=_fixed_clock,
                rng=random.Random(0),
            )

    def test_group_partial_stores_missing_group_store_raises(self) -> None:
        rng = random.Random(1)
        sid1 = StationId(uuid4())
        group_id = StationGroupId(uuid4())

        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        forcing_source = FakeWeatherReanalysisSource()
        hindcast_store = FakeHindcastStore()
        basin_store = FakeBasinStore()

        station = make_station_config(station_id=sid1)
        station_store.store_station(station)
        station_store.store_weather_source(_make_weather_source(sid1))

        with pytest.raises(ValueError, match="Required dependencies are None"):
            run_hindcast_flow.fn(
                model_id=_MODEL_ID,
                artifact_id=ArtifactId(uuid4()),
                group_id=group_id,
                period_start=_PERIOD_START,
                period_end=_PERIOD_END,
                time_step=_STEP,
                model=FakeGroupForecastModel(),
                artifact=b"fake_artifact",
                forcing_source=forcing_source,
                obs_store=obs_store,
                hindcast_store=hindcast_store,
                station_store=station_store,
                group_store=None,  # missing
                basin_store=basin_store,
                clock=_fixed_clock,
                rng=rng,
            )
