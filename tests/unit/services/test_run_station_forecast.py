from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.run_station_forecast import (
    MultiModelForecastResult,
    OperationalInputMetadata,
    StationForecastResult,
    run_all_station_forecasts,
    run_station_forecast,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet, QcFlag
from sapphire_flow.types.enums import (
    ModelArtifactStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    QcStatus,
    WarmUpSource,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs
from sapphire_flow.types.station import ModelAssignment
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import FakeModelArtifactStore

if TYPE_CHECKING:
    from sapphire_flow.types.ensemble import ForecastEnsemble

_NOW = ensure_utc(datetime(2025, 6, 1, 6, 0, tzinfo=UTC))
_STEP = timedelta(hours=24)
_STATION_ID = StationId(uuid4())
_MODEL_ID_A = ModelId("model-a")
_MODEL_ID_B = ModelId("model-b")
_RNG = random.Random(42)


def _make_config() -> DeploymentConfig:
    return DeploymentConfig(
        max_retention_days=1000,
        observation_staleness_warning_hours=6.0,
    )


def _make_metadata(
    warm_up_source: WarmUpSource = WarmUpSource.FRESH,
    observation_staleness_hours: float | None = 1.0,
    nwp_age_hours: float = 0.5,
    prior_state: bytes | None = None,
) -> OperationalInputMetadata:
    return OperationalInputMetadata(
        warm_up_source=warm_up_source,
        warm_up_state_age_hours=None,
        observation_staleness_hours=observation_staleness_hours,
        prior_state=prior_state,
        nwp_age_hours=nwp_age_hours,
    )


def _make_inputs() -> StationModelInputs:
    rows = [{"timestamp": _NOW - timedelta(hours=i), "value": 10.0} for i in range(10)]
    obs_df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    empty_df = pl.DataFrame({"timestamp": []}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=obs_df,
            past_dynamic=empty_df,
            future_dynamic=empty_df,
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=5,
        time_step=_STEP,
    )


def _make_assignment(model_id: ModelId, priority: int = 1) -> ModelAssignment:
    return ModelAssignment(
        station_id=_STATION_ID,
        model_id=model_id,
        time_step=_STEP,
        status=ModelAssignmentStatus.ACTIVE,
        priority=priority,
        created_at=_NOW,
    )


def _seed_artifact(store: FakeModelArtifactStore, model_id: ModelId) -> ArtifactId:
    aid, _ = store.store_artifact(
        model_id=model_id,
        artifact_bytes=b"fake_artifact",
        training_period_start=_NOW,
        training_period_end=_NOW,
        trained_at=_NOW,
        station_id=_STATION_ID,
        status=ModelArtifactStatus.ACTIVE,
    )
    return aid


def _empty_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0", rules=())


def _fixed_clock() -> object:
    def clock() -> object:
        return _NOW

    return clock


def _sequential_id_gen() -> object:
    ids = [uuid4() for _ in range(20)]
    idx = [0]

    def gen() -> UUID:
        val = ids[idx[0]]
        idx[0] += 1
        return val

    return gen


class TestHappyPath:
    def test_single_model_returns_result(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        model = FakeStationForecastModel()

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: model},  # type: ignore[dict-item]
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is not None
        assert isinstance(result, StationForecastResult)
        assert result.station_id == _STATION_ID
        assert result.model_id == _MODEL_ID_A
        assert len(result.forecasts) == 1
        assert "discharge" in result.ensembles

    def test_forecast_fields_populated(self) -> None:
        store = FakeModelArtifactStore()
        artifact_id = _seed_artifact(store, _MODEL_ID_A)
        model = FakeStationForecastModel()
        meta = _make_metadata(
            warm_up_source=WarmUpSource.FRESH,
            observation_staleness_hours=2.0,
            nwp_age_hours=1.0,
        )

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=meta,
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: model},  # type: ignore[dict-item]
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is not None
        fc = result.forecasts[0]
        assert fc.nwp_cycle_reference_time == _NOW
        assert fc.nwp_cycle_source == NwpCycleSource.PRIMARY
        assert fc.warm_up_source == WarmUpSource.FRESH
        assert fc.observation_staleness_hours == 2.0
        assert fc.model_artifact_id == artifact_id
        assert fc.version == 1
        assert fc.created_at == _NOW
        assert fc.updated_at == _NOW


class TestMultiModelFallback:
    def test_first_model_predict_raises_falls_through_to_second(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        class _FailingModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("model crashed")

            def serialize_artifact(self, artifact: object) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> object:
                return raw

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: _FailingModel(),  # type: ignore[dict-item]
                _MODEL_ID_B: FakeStationForecastModel(),  # type: ignore[dict-item]
            },
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is not None
        assert result.model_id == _MODEL_ID_B

    def test_priority_order_respected(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_B, priority=2),
                _make_assignment(_MODEL_ID_A, priority=1),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),  # type: ignore[dict-item]
                _MODEL_ID_B: FakeStationForecastModel(),  # type: ignore[dict-item]
            },
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is not None
        assert result.model_id == _MODEL_ID_A


class TestQcFailureFallback:
    def test_qc_failed_ensemble_falls_through_to_next_model(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        class _AlwaysFailQcChecker:
            def check(
                self,
                ensemble: ForecastEnsemble,
                rule_set: ForecastQcRuleSet,
                overrides: list,
                baselines: list,
            ) -> list[QcFlag]:
                return [
                    QcFlag(
                        rule_id="test_rule",
                        rule_version="1.0",
                        status=QcStatus.QC_FAILED,
                        detail="always fails",
                    )
                ]

        class _PassQcChecker:
            def check(
                self,
                ensemble: ForecastEnsemble,
                rule_set: ForecastQcRuleSet,
                overrides: list,
                baselines: list,
            ) -> list[QcFlag]:
                return []

        call_count = [0]

        class _FirstFailThenPassChecker:
            def check(
                self,
                ensemble: ForecastEnsemble,
                rule_set: ForecastQcRuleSet,
                overrides: list,
                baselines: list,
            ) -> list[QcFlag]:
                call_count[0] += 1
                if call_count[0] == 1:
                    return [
                        QcFlag(
                            rule_id="test_rule",
                            rule_version="1.0",
                            status=QcStatus.QC_FAILED,
                            detail="first model fails QC",
                        )
                    ]
                return []

        checker = _FirstFailThenPassChecker()

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),  # type: ignore[dict-item]
                _MODEL_ID_B: FakeStationForecastModel(),  # type: ignore[dict-item]
            },
            artifact_store=store,
            qc_checker=checker,  # type: ignore[arg-type]
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is not None
        assert result.model_id == _MODEL_ID_B
        fc = result.forecasts[0]
        assert fc.qc_status == QcStatus.QC_PASSED


class TestAllModelsFail:
    def test_returns_none_when_all_models_fail(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        class _AlwaysCrash:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(self, *args: object, **kwargs: object) -> None:
                raise ValueError("always crashes")

            def serialize_artifact(self, artifact: object) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> object:
                return raw

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: _AlwaysCrash(),  # type: ignore[dict-item]
                _MODEL_ID_B: _AlwaysCrash(),  # type: ignore[dict-item]
            },
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is None

    def test_returns_none_when_no_artifact(self) -> None:
        store = FakeModelArtifactStore()  # no artifacts seeded

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: FakeStationForecastModel()},  # type: ignore[dict-item]
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is None

    def test_returns_none_when_model_not_in_registry(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={},  # model registry is empty
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
        )

        assert result is None


_MODEL_ID_C = ModelId("model-c")


def _run_all(
    assignments: list,
    models: dict,
    store: FakeModelArtifactStore,
    qc_checker: object = None,
) -> MultiModelForecastResult:
    if qc_checker is None:
        qc_checker = ForecastOutputQualityChecker()
    return run_all_station_forecasts(
        station_id=_STATION_ID,
        inputs=_make_inputs(),
        input_metadata=_make_metadata(),
        assignments=assignments,
        models=models,  # type: ignore[arg-type]
        artifact_store=store,
        qc_checker=qc_checker,  # type: ignore[arg-type]
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines=[],
        nwp_cycle_reference_time=_NOW,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        config=_make_config(),
        clock=_fixed_clock(),  # type: ignore[arg-type]
        id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
        rng=random.Random(42),
    )


class TestRunAllStationForecasts:
    def test_all_models_succeed_returns_all_results(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        result = _run_all(
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
        )

        assert isinstance(result, MultiModelForecastResult)
        assert _MODEL_ID_A in result.results
        assert _MODEL_ID_B in result.results
        assert len(result.failed_models) == 0
        assert result.primary_model_id == _MODEL_ID_A

    def test_primary_is_highest_priority_success(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        result = _run_all(
            assignments=[
                _make_assignment(_MODEL_ID_B, priority=2),
                _make_assignment(_MODEL_ID_A, priority=1),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
        )

        assert result.primary_model_id == _MODEL_ID_A

    def test_first_model_fails_second_succeeds(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        class _FailingModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("model crashed")

            def serialize_artifact(self, artifact: object) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> object:
                return raw

        result = _run_all(
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: _FailingModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
        )

        assert _MODEL_ID_A in result.failed_models
        assert _MODEL_ID_B in result.results
        assert result.primary_model_id == _MODEL_ID_B

    def test_all_models_fail_empty_results_primary_none(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        class _AlwaysCrash:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(self, *args: object, **kwargs: object) -> None:
                raise ValueError("always crashes")

            def serialize_artifact(self, artifact: object) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> object:
                return raw

        result = _run_all(
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: _AlwaysCrash(),
                _MODEL_ID_B: _AlwaysCrash(),
            },
            store=store,
        )

        assert len(result.results) == 0
        assert result.primary_model_id is None
        assert _MODEL_ID_A in result.failed_models
        assert _MODEL_ID_B in result.failed_models

    def test_combinable_results_excludes_high_priority_fallbacks(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)
        _seed_artifact(store, _MODEL_ID_C)

        result = _run_all(
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=50),
                _make_assignment(_MODEL_ID_C, priority=90),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
                _MODEL_ID_C: FakeStationForecastModel(),
            },
            store=store,
        )

        combinable = result.combinable_results
        assert _MODEL_ID_A in combinable
        assert _MODEL_ID_B in combinable
        assert _MODEL_ID_C not in combinable


class _ShortHorizonNwpModel:
    """NWP-consuming model that (like ``NwpRegression``) emits
    ``horizon = len(future rows)`` — so a short ``future_dynamic`` silently
    truncates the forecast. Plan 090's coverage guard must skip it before it
    predicts. ensemble_mode=SINGLE so ``_run_single_model`` calls predict directly.
    """

    def __init__(self) -> None:
        from sapphire_flow.types.enums import (
            ArtifactScope,
            EnsembleMode,
            SpatialRepresentation,
        )
        from sapphire_flow.types.model import ModelDataRequirements

        self.artifact_scope = ArtifactScope.STATION
        self.data_requirements = ModelDataRequirements(
            target_parameters=frozenset({"discharge"}),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset({"precipitation", "temperature"}),
            static_features=frozenset(),
            supported_time_steps=frozenset({_STEP}),
            lookback_steps=1,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            ensemble_mode=EnsembleMode.SINGLE,
        )

    def train(self, *args: object, **kwargs: object) -> bytes:
        return b"artifact"

    def predict(
        self,
        artifact: object,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict, bytes | None]:
        from sapphire_flow.types.ensemble import ForecastEnsemble

        horizon = inputs.data.future_dynamic.height  # mirrors the truncation bug
        rows = []
        for step in range(horizon):
            vt = ensure_utc(inputs.issue_time + (step + 1) * inputs.time_step)
            for m in range(21):
                rows.append({"valid_time": vt, "member_id": m, "value": 5.0})
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
            pl.col("value").cast(pl.Float64),
        )
        ens = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m³/s",
            time_step=inputs.time_step,
            values=df,
        )
        return {"discharge": ens}, None

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


def _short_nwp_inputs(future_rows: int, n_members: int = 3) -> StationModelInputs:
    times = [ensure_utc(_NOW + (i + 1) * _STEP) for i in range(future_rows)]
    data: dict[str, list] = {"timestamp": times}
    for k in range(n_members):
        data[f"precipitation_{k}"] = [1.0] * future_rows
        data[f"temperature_{k}"] = [10.0] * future_rows
    future_dynamic = pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    obs_rows = [
        {"timestamp": _NOW - timedelta(hours=i), "value": 10.0} for i in range(10)
    ]
    obs_df = pl.DataFrame(obs_rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    empty = pl.DataFrame({"timestamp": []}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=obs_df,
            past_dynamic=empty,
            future_dynamic=future_dynamic,
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=5,
        time_step=_STEP,
    )


class TestNwpCoverageGuard:
    """Plan 090 D1/D2d/D3: an NWP-consuming model with under-covered future
    forcing is SKIPPED (never emits a truncated forecast); the native fallback
    model in the priority chain forecasts instead (runoff-only-style outcome).
    """

    _NWP_ID = ModelId("nwp_regression")
    _NATIVE_ID = ModelId("persistence_fallback")

    def _run_all(self, future_rows: int) -> MultiModelForecastResult:
        store = FakeModelArtifactStore()
        _seed_artifact(store, self._NWP_ID)
        _seed_artifact(store, self._NATIVE_ID)
        return run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_short_nwp_inputs(future_rows=future_rows),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(self._NWP_ID, priority=1),
                _make_assignment(self._NATIVE_ID, priority=2),
            ],
            models={
                self._NWP_ID: _ShortHorizonNwpModel(),  # type: ignore[dict-item]
                self._NATIVE_ID: FakeStationForecastModel(),  # type: ignore[dict-item]
            },
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=random.Random(42),
        )

    def test_short_coverage_skips_nwp_model_and_native_fallback_wins(self) -> None:
        # future_dynamic has 1 daily row; the NWP model needs 5 → skipped.
        result = self._run_all(future_rows=1)

        assert self._NWP_ID not in result.results
        assert self._NWP_ID in result.failed_models
        assert "insufficient NWP coverage" in result.failed_models[self._NWP_ID]
        # The native fallback (no future features) still forecasts, at full horizon.
        assert result.primary_model_id == self._NATIVE_ID
        native = result.results[self._NATIVE_ID]
        assert native.ensembles["discharge"].forecast_horizon_steps == 5

    def test_adequate_coverage_keeps_nwp_model(self) -> None:
        # 5 clean daily rows satisfy forecast_horizon_steps=5 → NWP model runs.
        result = self._run_all(future_rows=5)

        assert self._NWP_ID in result.results
        assert result.primary_model_id == self._NWP_ID


class _MixedMemberEnsembleNwpModel:
    """ensemble_mode=ENSEMBLE model whose future_dynamic carries one required
    feature member-suffixed and another as a bare column. Plan 090 D1 requires
    every required feature to carry the SAME non-empty member set for an ensemble
    model — a bare-only feature is inadequate (the fan-out would reuse the single
    bare value for every member)."""

    def __init__(self) -> None:
        from sapphire_flow.types.enums import (
            ArtifactScope,
            EnsembleMode,
            SpatialRepresentation,
        )
        from sapphire_flow.types.model import ModelDataRequirements

        self.artifact_scope = ArtifactScope.STATION
        self.data_requirements = ModelDataRequirements(
            target_parameters=frozenset({"discharge"}),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset({"precipitation", "temperature"}),
            static_features=frozenset(),
            supported_time_steps=frozenset({_STEP}),
            lookback_steps=1,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            ensemble_mode=EnsembleMode.ENSEMBLE,
        )

    def train(self, *args: object, **kwargs: object) -> bytes:
        return b"artifact"

    def predict(
        self,
        artifact: object,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict, bytes | None]:
        from sapphire_flow.types.ensemble import ForecastEnsemble

        # Reached only pre-fix (via fan-out per member slice → bare precipitation).
        fd = inputs.data.future_dynamic
        values = fd.select(
            pl.col("timestamp").alias("valid_time"),
            pl.lit(0).cast(pl.Int32).alias("member_id"),
            pl.col("precipitation").cast(pl.Float64).alias("value"),
        )
        ens = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m³/s",
            time_step=inputs.time_step,
            values=values,
        )
        return {"discharge": ens}, None

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


def _mixed_member_inputs(rows: int = 5, n_members: int = 3) -> StationModelInputs:
    times = [ensure_utc(_NOW + (i + 1) * _STEP) for i in range(rows)]
    data: dict[str, list] = {"timestamp": times, "temperature": [10.0] * rows}
    for k in range(n_members):
        data[f"precipitation_{k}"] = [1.0] * rows
    future_dynamic = pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    obs_rows = [
        {"timestamp": _NOW - timedelta(hours=i), "value": 10.0} for i in range(10)
    ]
    obs_df = pl.DataFrame(obs_rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    empty = pl.DataFrame({"timestamp": []}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=obs_df,
            past_dynamic=empty,
            future_dynamic=future_dynamic,
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=5,
        time_step=_STEP,
    )


class TestEnsembleMemberSetCoverage:
    """Plan 090 D1 (Finding 3): the coverage guard is member-set-aware. An
    ENSEMBLE model with a required feature present only as a bare column (no
    member suffix) has inadequate coverage and is skipped — even though each
    present column individually carries enough clean rows.
    """

    _NWP_ID = ModelId("nwp_ensemble")
    _NATIVE_ID = ModelId("persistence_fallback")

    def test_ensemble_bare_only_feature_is_skipped(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, self._NWP_ID)
        _seed_artifact(store, self._NATIVE_ID)
        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_mixed_member_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(self._NWP_ID, priority=1),
                _make_assignment(self._NATIVE_ID, priority=2),
            ],
            models={
                self._NWP_ID: _MixedMemberEnsembleNwpModel(),  # type: ignore[dict-item]
                self._NATIVE_ID: FakeStationForecastModel(),  # type: ignore[dict-item]
            },
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=random.Random(42),
        )

        assert self._NWP_ID not in result.results
        assert self._NWP_ID in result.failed_models
        assert "insufficient NWP coverage" in result.failed_models[self._NWP_ID]
        assert result.primary_model_id == self._NATIVE_ID


class TestSkillModelOutranksFallback:
    """Plan 089 regression: config-driven priorities make the PRIMARY chain
    prefer skill models over fallbacks.

    Before the fix, onboarding assigned every model priority=0, so the
    first-success chain reached a fallback (by arbitrary store order) before the
    skill model. Here priorities are resolved from DeploymentConfig.model_priorities
    exactly as onboarding Step 6 does; the fallback is listed FIRST to model the
    arbitrary fetch order that caused the live incident.
    """

    def test_skill_model_is_primary_over_fallback(self) -> None:
        skill_model_id = ModelId("nwp_rainfall_runoff")
        fallback_model_id = ModelId("climatology_fallback")

        config = DeploymentConfig(
            max_retention_days=1000,
            model_priorities={
                str(skill_model_id): 20,
                str(fallback_model_id): 100,
            },
        )

        store = FakeModelArtifactStore()
        _seed_artifact(store, skill_model_id)
        _seed_artifact(store, fallback_model_id)

        # Fallback ordered first — pre-fix (equal priorities) this wins the chain.
        result = _run_all(
            assignments=[
                _make_assignment(
                    fallback_model_id,
                    priority=config.priority_for_model(str(fallback_model_id)),
                ),
                _make_assignment(
                    skill_model_id,
                    priority=config.priority_for_model(str(skill_model_id)),
                ),
            ],
            models={
                skill_model_id: FakeStationForecastModel(),
                fallback_model_id: FakeStationForecastModel(),
            },
            store=store,
        )

        assert result.primary_model_id == skill_model_id
