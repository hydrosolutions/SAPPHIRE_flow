from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.run_station_forecast import (
    OperationalInputMetadata,
    StationForecastResult,
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
