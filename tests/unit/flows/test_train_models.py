from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.exceptions import ArtifactIntegrityError
from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.flows.train_models import train_models_flow
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.station import (
    ModelAssignment,
    StationGroup,
    StationWeatherSource,
)
from tests.conftest import make_observations, make_station_config
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeFlowRegimeConfigStore,
    FakeHindcastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeSkillStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_RNG = random.Random(42)
_EPOCH = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _seed_hindcasts_and_obs(
    hindcast_store: FakeHindcastStore,
    obs_store: FakeObservationStore,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    hindcast_run_id: UUID,
    parameter: str,
) -> None:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import (
        EnsembleRepresentation,
        ForcingType,
        ObservationSource,
        QcStatus,
    )
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import HindcastForecastId, ObservationId
    from sapphire_flow.types.observation import Observation

    units = "m³/s" if parameter == "discharge" else "m"
    time_step = timedelta(hours=1)

    for i in range(3):
        step = ensure_utc(datetime(2025, 2, i + 1, tzinfo=UTC))
        vt = ensure_utc(datetime(2025, 2, i + 1, 1, 0, tzinfo=UTC))
        df = pl.DataFrame(
            [{"valid_time": vt, "member_id": m, "value": 5.0 + m} for m in range(3)]
        ).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=step,
            parameter=parameter,
            units=units,
            time_step=time_step,
            values=df,
        )
        hc = HindcastForecast(
            id=HindcastForecastId(_uuid()),
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=artifact_id,
            hindcast_step=step,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=hindcast_run_id,
            ensemble=ensemble,
            created_at=step,
        )
        hindcast_store.store_hindcast(hc)
        obs = Observation(
            id=ObservationId(_uuid()),
            station_id=station_id,
            timestamp=vt,
            parameter=parameter,
            value=6.0,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.QC_PASSED,
            qc_flags=[],
            qc_rule_version=None,
            created_at=step,
        )
        obs_store.store_observations([obs])


_RNG_SEED = 42
_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_TRAINING_START = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))
_TRAINING_END = ensure_utc(datetime(2024, 12, 31, tzinfo=UTC))
_N_OBS_DAYS = 365 * 3 + 10


def _make_forcing_records(
    station_id: StationId,
    start: object,
    n_days: int,
) -> list:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

    records = []
    for i in range(n_days):
        ts = ensure_utc(datetime.fromtimestamp(start.timestamp() + i * 86400, tz=UTC))
        for param in ("precipitation", "temperature"):
            records.append(
                RawHistoricalForcing(
                    station_id=station_id,
                    source="smn",
                    version="1.0",
                    valid_time=ts,
                    parameter=param,
                    spatial_type=SpatialRepresentation.POINT,
                    band_id=None,
                    member_id=None,
                    value=5.0 if param == "precipitation" else 10.0,
                )
            )
    return records


def _setup_station_stores(
    station_id: StationId,
    model_id: ModelId,
) -> tuple:
    rng = random.Random(_RNG_SEED)
    model_store = FakeModelStore()
    station_store = FakeStationStore()
    group_store = FakeStationGroupStore()
    obs_store = FakeObservationStore()
    basin_store = FakeBasinStore()
    artifact_store = FakeModelArtifactStore()
    hindcast_store = FakeHindcastStore()
    skill_store = FakeSkillStore()
    flow_regime_store = FakeFlowRegimeConfigStore()

    station = make_station_config(station_id=station_id)
    station_store.store_station(station)

    assignment = ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=timedelta(days=1),
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_EPOCH,
    )
    station_store.store_model_assignment(assignment)

    weather_source = StationWeatherSource(
        station_id=station_id,
        nwp_source="smn",
        extraction_type=SpatialRepresentation.POINT,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )
    station_store.store_weather_source(weather_source)

    obs = make_observations(
        n=_N_OBS_DAYS,
        station_id=station_id,
        parameter="discharge",
        start=_TRAINING_START,
        interval=timedelta(days=1),
        rng=rng,
    )
    obs_store.store_observations(obs)

    forcing_records = _make_forcing_records(station_id, _TRAINING_START, _N_OBS_DAYS)
    forcing_source = FakeWeatherReanalysisSource(records=forcing_records)

    return (
        model_store,
        station_store,
        group_store,
        obs_store,
        basin_store,
        artifact_store,
        hindcast_store,
        skill_store,
        flow_regime_store,
        forcing_source,
    )


def _flow_kwargs(
    model_id: ModelId,
    model: object,
    model_store: object,
    station_store: object,
    group_store: object,
    obs_store: object,
    basin_store: object,
    artifact_store: object,
    hindcast_store: object,
    skill_store: object,
    flow_regime_store: object,
    forcing_source: object,
) -> dict:
    return dict(
        period_start=str(_TRAINING_START.isoformat()),
        period_end=str(_TRAINING_END.isoformat()),
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
        obs_store=obs_store,
        basin_store=basin_store,
        artifact_store=artifact_store,
        hindcast_store=hindcast_store,
        skill_store=skill_store,
        flow_regime_store=flow_regime_store,
        forcing_source=forcing_source,
        models={model_id: model},
        clock=lambda: _EPOCH,
        rng=random.Random(0),
    )


class TestTrainModelsFlowHappyPath:
    def test_station_model_stores_artifact_and_computes_skill(self) -> None:
        rng = random.Random(_RNG_SEED)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        model_id = ModelId("fake_station_model")
        model = FakeStationForecastModel()

        (
            model_store,
            station_store,
            group_store,
            obs_store,
            basin_store,
            artifact_store,
            hindcast_store,
            skill_store,
            flow_regime_store,
            forcing_source,
        ) = _setup_station_stores(station_id, model_id)

        results = train_models_flow(
            **_flow_kwargs(
                model_id,
                model,
                model_store,
                station_store,
                group_store,
                obs_store,
                basin_store,
                artifact_store,
                hindcast_store,
                skill_store,
                flow_regime_store,
                forcing_source,
            )
        )

        assert len(results) == 1
        result = results[0]
        assert result.error is None
        assert result.artifact_id is not None
        assert result.skill_computed is True
        assert len(hindcast_store._hindcasts) > 0
        assert len(skill_store._scores) > 0


class TestTrainModelsFlowModelNotFound:
    def test_model_not_in_registry_returns_error_result(self) -> None:
        rng = random.Random(_RNG_SEED + 1)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        model_id = ModelId("fake_station_model")
        ghost_model_id = ModelId("ghost_model_that_does_not_exist")

        model_store = FakeModelStore()
        station_store = FakeStationStore()
        group_store = FakeStationGroupStore()
        obs_store = FakeObservationStore()
        basin_store = FakeBasinStore()
        artifact_store = FakeModelArtifactStore()
        hindcast_store = FakeHindcastStore()
        skill_store = FakeSkillStore()
        flow_regime_store = FakeFlowRegimeConfigStore()

        station = make_station_config(station_id=station_id)
        station_store.store_station(station)
        assignment = ModelAssignment(
            station_id=station_id,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_EPOCH,
        )
        station_store.store_model_assignment(assignment)
        station_store.store_weather_source(
            StationWeatherSource(
                station_id=station_id,
                nwp_source="smn",
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )

        # Register model_id in the model_store so scope yields one unit for model_id,
        # but pass models={ghost_model_id: ...} so the flow can't find model_id
        # in the models dict → TrainingResult.error is set.
        from sapphire_flow.services.model_registry import register_models

        real_model = FakeStationForecastModel()
        register_models({model_id: real_model}, model_store, lambda: _EPOCH)
        forcing_source = FakeWeatherReanalysisSource()

        results = train_models_flow(
            period_start=str(_TRAINING_START.isoformat()),
            period_end=str(_TRAINING_END.isoformat()),
            model_store=model_store,
            station_store=station_store,
            group_store=group_store,
            obs_store=obs_store,
            basin_store=basin_store,
            artifact_store=artifact_store,
            hindcast_store=hindcast_store,
            skill_store=skill_store,
            flow_regime_store=flow_regime_store,
            forcing_source=forcing_source,
            models={ghost_model_id: real_model},
            clock=lambda: _EPOCH,
            rng=random.Random(0),
        )

        assert len(results) == 1
        result = results[0]
        assert result.error is not None
        assert "not found" in result.error.lower()
        assert result.artifact_id is None
        assert result.skill_computed is False


class TestTrainModelsFlowGroupModel:
    def test_group_model_training_stores_artifact(self) -> None:
        rng = random.Random(_RNG_SEED + 2)

        def _next_uuid() -> UUID:
            return UUID(int=rng.getrandbits(128), version=4)

        station_id_1 = StationId(_next_uuid())
        station_id_2 = StationId(_next_uuid())
        group_id = StationGroupId(_next_uuid())
        model_id = ModelId("fake_group_model")
        model = FakeGroupForecastModel()

        model_store = FakeModelStore()
        inner_station_store = FakeStationStore()
        group_store = FakeStationGroupStore()
        obs_store = FakeObservationStore()
        basin_store = FakeBasinStore()
        artifact_store = FakeModelArtifactStore(group_store=group_store)
        hindcast_store = FakeHindcastStore()
        skill_store = FakeSkillStore()
        flow_regime_store = FakeFlowRegimeConfigStore()

        group = StationGroup(
            id=group_id,
            name="test-group",
            station_ids=frozenset({station_id_1, station_id_2}),
            created_at=_EPOCH,
        )
        group_store.store_group(group)

        from sapphire_flow.services.model_registry import register_models
        from sapphire_flow.types.station import GroupModelAssignment

        register_models({model_id: model}, model_store, lambda: _EPOCH)

        group_assignment = GroupModelAssignment(
            group_id=group_id,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_EPOCH,
        )
        group_store.store_group_model_assignment(group_assignment)
        group_store.seed_group_model_assignment(group_id, model_id, group_assignment)

        all_records = []
        for sid in (station_id_1, station_id_2):
            st = make_station_config(station_id=sid)
            inner_station_store.store_station(st)
            inner_station_store.store_weather_source(
                StationWeatherSource(
                    station_id=sid,
                    nwp_source="smn",
                    extraction_type=SpatialRepresentation.POINT,
                    status=WeatherSourceStatus.ACTIVE,
                    role=WeatherSourceRole.REANALYSIS,
                )
            )
            obs = make_observations(
                n=_N_OBS_DAYS,
                station_id=sid,
                parameter="discharge",
                start=_TRAINING_START,
                interval=timedelta(days=1),
                rng=random.Random(_RNG_SEED),
            )
            obs_store.store_observations(obs)
            all_records.extend(_make_forcing_records(sid, _TRAINING_START, _N_OBS_DAYS))

        forcing_source = FakeWeatherReanalysisSource(records=all_records)

        results = train_models_flow(
            period_start=str(_TRAINING_START.isoformat()),
            period_end=str(_TRAINING_END.isoformat()),
            model_store=model_store,
            station_store=inner_station_store,
            group_store=group_store,
            obs_store=obs_store,
            basin_store=basin_store,
            artifact_store=artifact_store,
            hindcast_store=hindcast_store,
            skill_store=skill_store,
            flow_regime_store=flow_regime_store,
            forcing_source=forcing_source,
            models={model_id: model},
            clock=lambda: _EPOCH,
            rng=random.Random(0),
        )

        assert len(results) == 1
        result = results[0]
        assert result.error is None
        assert result.artifact_id is not None
        assert result.training_unit.group_id == group_id


class TestTrainModelsFlowArtifactIntegrity:
    def test_sha256_round_trip_stored_bytes_match(self) -> None:
        rng = random.Random(_RNG_SEED + 3)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        model_id = ModelId("fake_station_model")
        model = FakeStationForecastModel()

        (
            model_store,
            station_store,
            group_store,
            obs_store,
            basin_store,
            artifact_store,
            hindcast_store,
            skill_store,
            flow_regime_store,
            forcing_source,
        ) = _setup_station_stores(station_id, model_id)

        results = train_models_flow(
            **_flow_kwargs(
                model_id,
                model,
                model_store,
                station_store,
                group_store,
                obs_store,
                basin_store,
                artifact_store,
                hindcast_store,
                skill_store,
                flow_regime_store,
                forcing_source,
            )
        )

        assert len(results) == 1
        artifact_id = results[0].artifact_id
        assert artifact_id is not None

        fetched = artifact_store.fetch_artifact(artifact_id)
        assert fetched is not None
        _, fetched_bytes = fetched

        rec = artifact_store.fetch_artifact_record(artifact_id)
        assert rec is not None
        assert hashlib.sha256(fetched_bytes).hexdigest() == rec.sha256_hash

    def test_store_layer_corrupted_bytes_raises_artifact_integrity_error(self) -> None:
        rng = random.Random(_RNG_SEED + 4)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        model_id = ModelId("fake_station_model")
        model = FakeStationForecastModel()

        (
            model_store,
            station_store,
            group_store,
            obs_store,
            basin_store,
            artifact_store,
            hindcast_store,
            skill_store,
            flow_regime_store,
            forcing_source,
        ) = _setup_station_stores(station_id, model_id)

        results = train_models_flow(
            **_flow_kwargs(
                model_id,
                model,
                model_store,
                station_store,
                group_store,
                obs_store,
                basin_store,
                artifact_store,
                hindcast_store,
                skill_store,
                flow_regime_store,
                forcing_source,
            )
        )

        artifact_id = results[0].artifact_id
        assert artifact_id is not None

        # Corrupt the stored bytes after the flow completes
        artifact_store._bytes[artifact_id] = b"tampered_garbage"

        with pytest.raises(ArtifactIntegrityError):
            artifact_store.fetch_artifact(artifact_id)

    def test_flow_layer_spy_store_sha256_mismatch_raises_value_error(self) -> None:
        rng = random.Random(_RNG_SEED + 5)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        model_id = ModelId("fake_station_model")
        model = FakeStationForecastModel()

        class _CorruptingArtifactStore:
            def __init__(self, inner: FakeModelArtifactStore) -> None:
                self._inner = inner

            def store_artifact(self, *args: object, **kwargs: object) -> object:
                return self._inner.store_artifact(*args, **kwargs)

            def fetch_artifact(
                self, artifact_id: ArtifactId
            ) -> tuple[ArtifactId, bytes] | None:
                result = self._inner.fetch_artifact(artifact_id)
                if result is None:
                    return None
                aid, _ = result
                return aid, b"silently_corrupted_bytes"

            def fetch_active_artifact(self, *args: object, **kwargs: object) -> object:
                return self._inner.fetch_active_artifact(*args, **kwargs)

            def fetch_artifact_record(self, *args: object, **kwargs: object) -> object:
                return self._inner.fetch_artifact_record(*args, **kwargs)

            def fetch_artifacts_by_status(
                self, *args: object, **kwargs: object
            ) -> object:
                return self._inner.fetch_artifacts_by_status(*args, **kwargs)

            def transition_artifact_status(
                self, *args: object, **kwargs: object
            ) -> None:
                self._inner.transition_artifact_status(*args, **kwargs)

            def fetch_active_artifact_for_station(
                self, *args: object, **kwargs: object
            ) -> object:
                return self._inner.fetch_active_artifact_for_station(*args, **kwargs)

        inner_store = FakeModelArtifactStore()

        (
            model_store,
            station_store,
            group_store,
            obs_store,
            basin_store,
            _,
            hindcast_store,
            skill_store,
            flow_regime_store,
            forcing_source,
        ) = _setup_station_stores(station_id, model_id)

        spy_store = _CorruptingArtifactStore(inner_store)

        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            train_models_flow(
                period_start=str(_TRAINING_START.isoformat()),
                period_end=str(_TRAINING_END.isoformat()),
                model_store=model_store,
                station_store=station_store,
                group_store=group_store,
                obs_store=obs_store,
                basin_store=basin_store,
                artifact_store=spy_store,
                hindcast_store=hindcast_store,
                skill_store=skill_store,
                flow_regime_store=flow_regime_store,
                forcing_source=forcing_source,
                models={model_id: model},
                clock=lambda: _EPOCH,
                rng=random.Random(0),
            )


class TestMultiParameterImport:
    def test_compute_skills_task_is_importable(self) -> None:
        assert hasattr(compute_skills_task, "map")

    def test_compute_skills_task_has_fn(self) -> None:
        assert callable(compute_skills_task.fn)


class TestMultiParameterSkillComputation:
    def test_computes_skills_for_all_target_parameters(self) -> None:
        target_parameters = frozenset({"discharge", "water_level"})
        station_ids = [StationId(_uuid()), StationId(_uuid())]
        model_id = ModelId("multi-param-model")
        artifact_id = ArtifactId(_uuid())
        hindcast_run_id = _uuid()
        clock = lambda: _EPOCH  # noqa: E731

        hindcast_store = FakeHindcastStore()
        obs_store = FakeObservationStore()
        skill_store = FakeSkillStore()
        station_store = FakeStationStore()

        for sid in station_ids:
            for param in target_parameters:
                _seed_hindcasts_and_obs(
                    hindcast_store=hindcast_store,
                    obs_store=obs_store,
                    station_id=sid,
                    model_id=model_id,
                    artifact_id=artifact_id,
                    hindcast_run_id=hindcast_run_id,
                    parameter=param,
                )

        skill_pairs = [
            (sid, param) for sid in station_ids for param in sorted(target_parameters)
        ]
        for sid, param in skill_pairs:
            compute_skills_task.fn(
                station_id=sid,
                model_id=model_id,
                artifact_id=artifact_id,
                parameter=param,
                hindcast_run_id=hindcast_run_id,
                hindcast_store=hindcast_store,
                obs_store=obs_store,
                skill_store=skill_store,
                station_store=station_store,
                flow_regime_store=None,
                deployment_config=None,
                clock=clock,
            )

        stored_params = {s.parameter for s in skill_store._scores}
        stored_stations = {s.station_id for s in skill_store._scores}

        assert stored_params == target_parameters
        assert stored_stations == set(station_ids)


class TestBootstrapPath:
    def test_bootstrap_resolves_stores_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock, patch

        from sapphire_flow.types.training import TrainingScope

        stores_dict = {
            "model_store": MagicMock(),
            "station_store": MagicMock(),
            "group_store": MagicMock(),
            "obs_store": MagicMock(),
            "basin_store": MagicMock(),
            "artifact_store": MagicMock(),
            "hindcast_store": MagicMock(),
            "skill_store": MagicMock(),
            "flow_regime_store": MagicMock(),
            "forcing_store": MagicMock(),
        }
        captured: dict[str, object] = {}

        def fake_setup(url: str) -> tuple[object, dict]:
            captured["url"] = url
            return (MagicMock(), stores_dict)

        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores", fake_setup
        )

        # Spy on the real select_reanalysis_source (Plan 115a §6 single
        # factory) so we can prove the bootstrapped forcing_store actually
        # flows into a real reanalysis source, rather than the flow merely
        # tolerating a missing "forcing_store" key.
        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source as real_select_reanalysis_source,
        )

        reanalysis_calls: list[dict[str, object]] = []

        def spy_select_reanalysis_source(
            *, forcing_store: object, mode: object
        ) -> object:
            source = real_select_reanalysis_source(
                forcing_store=forcing_store,
                mode=mode,  # type: ignore[arg-type]
            )
            reanalysis_calls.append({"forcing_store": forcing_store, "source": source})
            return source

        monkeypatch.setattr(
            "sapphire_flow.adapters.hybrid_reanalysis_factories.select_reanalysis_source",
            spy_select_reanalysis_source,
        )

        with (
            patch(
                "sapphire_flow.flows.train_models.discover_models",
                return_value={},
            ),
            patch("sapphire_flow.flows.train_models.register_models") as mock_register,
            patch(
                "sapphire_flow.flows.train_models._determine_scope_task",
                return_value=TrainingScope(units=()),
            ),
        ):
            results = train_models_flow.fn(
                clock=lambda: _EPOCH,
                rng=random.Random(0),
            )

        assert captured["url"] == "sqlite://"
        assert results == []
        # register_models was called with the bootstrapped model_store
        assert mock_register.called
        args, _ = mock_register.call_args
        assert args[1] is stores_dict["model_store"]

        # The bootstrap path wired the bootstrapped forcing_store into a real
        # reanalysis source via select_reanalysis_source (Plan 115a §6).
        assert len(reanalysis_calls) == 1
        assert reanalysis_calls[0]["forcing_store"] is stores_dict["forcing_store"]
        from sapphire_flow.adapters.store_backed_reanalysis import (
            StoreBackedReanalysisSource,
        )

        assert isinstance(reanalysis_calls[0]["source"], StoreBackedReanalysisSource)
