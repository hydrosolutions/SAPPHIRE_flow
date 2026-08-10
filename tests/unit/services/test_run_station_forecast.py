from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
from structlog.testing import capture_logs

import sapphire_flow.services.run_station_forecast as rsf_module
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.run_station_forecast import (
    MultiModelForecastResult,
    OperationalInputMetadata,
    StationForecastResult,
    run_all_station_forecasts,
    run_station_forecast,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleParams, ForecastQcRuleSet, QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ArtifactScope,
    EnsembleMode,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    QcStatus,
    SpatialRepresentation,
    WarmUpSource,
)
from sapphire_flow.types.ids import (
    CLIMATOLOGY_FALLBACK_MODEL_ID,
    ArtifactId,
    ModelId,
    StationId,
)
from sapphire_flow.types.model import (
    ModelDataRequirements,
    StationInputData,
    StationModelInputs,
)
from sapphire_flow.types.station import ModelAssignment
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import FakeModelArtifactStore, FakeModelStateStore

if TYPE_CHECKING:
    from pytest import MonkeyPatch

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
) -> OperationalInputMetadata:
    return OperationalInputMetadata(
        warm_up_source=warm_up_source,
        warm_up_state_age_hours=None,
        observation_staleness_hours=observation_staleness_hours,
        nwp_age_hours=nwp_age_hours,
    )


def _make_inputs(issue_time: UtcDatetime = _NOW) -> StationModelInputs:
    rows = [
        {"timestamp": issue_time - timedelta(hours=i), "value": 10.0} for i in range(10)
    ]
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
        issue_time=issue_time,
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


def _water_level_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(
        version="1.0",
        rules=(
            ForecastQcRuleParams(
                rule_id="range_check",
                rule_version="1.0",
                parameter="water_level",
                time_step=_STEP,
                thresholds={"value_min": -2.0, "value_max": 20.0},
            ),
        ),
    )


class _WaterLevelModel(FakeStationForecastModel):
    parameter = "water_level"
    units = "m"

    def predict(
        self,
        artifact: object,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        rows = [
            {
                "valid_time": inputs.issue_time + (step + 1) * inputs.time_step,
                "member_id": member,
                "value": 261.0,
            }
            for step in range(inputs.forecast_horizon_steps)
            for member in range(3)
        ]
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="water_level",
            units="m",
            time_step=inputs.time_step,
            values=df,
        )
        return {"water_level": ensemble}, None


def _fixed_clock() -> object:
    def clock() -> object:
        return _NOW

    return clock


def _ticking_clock(start: object, step: timedelta) -> tuple[object, list[object]]:
    """A clock that returns a NEW value on every call and records every call
    it made. A fixed clock (``_fixed_clock``) cannot distinguish "called once"
    from "called twice, same instant" — this can, which is what a golden test
    for D4 (byte-identical timestamps under ANY clock, exactly one runner
    ``clock()`` call on an empty state store) needs.
    """
    current: list[object] = [start]
    calls: list[object] = []

    def clock() -> object:
        value = current[0]
        calls.append(value)
        current[0] = value + step  # type: ignore[operator]
        return value

    return clock, calls


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
            model_state_store=FakeModelStateStore(),
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
            observation_staleness_hours=2.0,
            nwp_age_hours=1.0,
        )
        # The forecast's warm_up_source is now read PER-ASSIGNMENT from the
        # store (Plan 148 D3) — not carried on ``input_metadata`` — so seed a
        # FRESH state (< 24h) for this assignment's own model_id.
        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_A, _NOW - timedelta(hours=1), b"warm_state"
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
            model_state_store=state_store,
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

    def test_water_level_qc_uses_relative_stage_when_datum_present(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: _WaterLevelModel()},  # type: ignore[dict-item]
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_water_level_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
            model_state_store=FakeModelStateStore(),
            water_level_datum_masl=260.0,
        )

        assert result is not None
        forecast = result.forecasts[0]
        assert forecast.qc_status == QcStatus.QC_PASSED
        assert forecast.ensemble.values["value"].min() == 261.0

    def test_water_level_qc_skips_range_check_when_datum_missing(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: _WaterLevelModel()},  # type: ignore[dict-item]
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_water_level_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=_RNG,
            model_state_store=FakeModelStateStore(),
            water_level_datum_masl=None,
        )

        assert result is not None
        assert result.forecasts[0].qc_status == QcStatus.QC_PASSED


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
            model_state_store=FakeModelStateStore(),
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
            model_state_store=FakeModelStateStore(),
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
            model_state_store=FakeModelStateStore(),
        )

        assert result is not None
        assert result.model_id == _MODEL_ID_B
        fc = result.forecasts[0]
        assert fc.qc_status == QcStatus.QC_PASSED

    def test_qc_failed_returns_typed_failure_via_run_all(self) -> None:
        """Red-first (T1 item 4): the same QC-fallback scenario as above,
        called via ``run_all_station_forecasts`` directly, asserting on the
        FAILED assignment's recorded ``.cause`` (not just that the fallback
        succeeded).
        """
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

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
            qc_checker=_FirstFailThenPassChecker(),
        )

        assert result.primary_model_id == _MODEL_ID_B
        failure = result.failed_models[_MODEL_ID_A]
        assert isinstance(failure, rsf_module.AssignmentFailure)
        assert failure.cause is rsf_module.AssignmentFailureCause.QC_FAILED


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
            model_state_store=FakeModelStateStore(),
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
            model_state_store=FakeModelStateStore(),
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
            model_state_store=FakeModelStateStore(),
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
        model_state_store=FakeModelStateStore(),
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
        assert (
            result.failed_models[_MODEL_ID_A].cause
            is rsf_module.AssignmentFailureCause.PREDICT_FAILED
        )
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
        assert (
            result.failed_models[_MODEL_ID_A].cause
            is rsf_module.AssignmentFailureCause.PREDICT_FAILED
        )
        assert (
            result.failed_models[_MODEL_ID_B].cause
            is rsf_module.AssignmentFailureCause.PREDICT_FAILED
        )

    def test_combinable_results_uses_fallback_membership_not_priority(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)
        _seed_artifact(store, _MODEL_ID_C)
        _seed_artifact(store, CLIMATOLOGY_FALLBACK_MODEL_ID)

        result = _run_all(
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=50),
                _make_assignment(_MODEL_ID_C, priority=90),
                _make_assignment(CLIMATOLOGY_FALLBACK_MODEL_ID, priority=0),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
                _MODEL_ID_C: FakeStationForecastModel(),
                CLIMATOLOGY_FALLBACK_MODEL_ID: FakeStationForecastModel(),
            },
            store=store,
        )

        combinable = result.combinable_results
        assert _MODEL_ID_A in combinable
        assert _MODEL_ID_B in combinable
        assert _MODEL_ID_C in combinable
        assert CLIMATOLOGY_FALLBACK_MODEL_ID not in combinable


class _RaisingForModelArtifactStore:
    """A ``ModelArtifactStore`` fake whose ``fetch_active_artifact_for_station``
    raises for one specific ``model_id`` — a call OUTSIDE
    ``_run_single_model``'s two guarded regions (the warm-up ``try`` and the
    deserialize/predict ``try``), used to prove the loop-level backstop (D3)
    is load-bearing: an unanticipated exception here must not escape
    ``run_all_station_forecasts`` and discard an already-succeeded
    higher-priority primary.
    """

    def __init__(self, inner: FakeModelArtifactStore, raise_for: ModelId) -> None:
        self._inner = inner
        self._raise_for = raise_for

    def fetch_active_artifact_for_station(
        self, station_id: StationId, model_id: ModelId
    ) -> tuple[ArtifactId, bytes] | None:
        if model_id == self._raise_for:
            raise RuntimeError("unexpected artifact-store failure")
        return self._inner.fetch_active_artifact_for_station(station_id, model_id)


class TestAssignmentOutcomeShape:
    """Plan 150 T1 — red-first proof that ``_run_single_model`` /
    ``run_all_station_forecasts`` return a discriminated ``AssignmentOutcome``
    (``AssignmentSuccess | AssignmentFailure``) instead of
    ``StationForecastResult | str``, and that the loop-level backstop (D3)
    closes the fallback-invariant gap (Problem #3 / D6).
    """

    def test_no_active_artifact_returns_typed_failure(self) -> None:
        store = FakeModelArtifactStore()  # no artifacts seeded

        result = _run_all(
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: FakeStationForecastModel()},
            store=store,
        )

        failure = result.failed_models[_MODEL_ID_A]
        assert isinstance(failure, rsf_module.AssignmentFailure)
        assert failure.cause is rsf_module.AssignmentFailureCause.NO_ARTIFACT

    def test_successful_assignment_wraps_in_assignment_success(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        outcome = rsf_module._run_single_model(
            station_id=_STATION_ID,
            assignment=_make_assignment(_MODEL_ID_A),
            inputs=_make_inputs(),
            observation_staleness_hours=1.0,
            nwp_age_hours=0.5,
            model_state_store=FakeModelStateStore(),
            models={_MODEL_ID_A: FakeStationForecastModel()},  # type: ignore[dict-item]
            artifact_store=store,
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=random.Random(42),
        )

        assert isinstance(outcome, rsf_module.AssignmentSuccess)
        assert isinstance(outcome.result, StationForecastResult)
        assert outcome.result.model_id == _MODEL_ID_A

    def test_model_not_found_returns_typed_failure(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        result = _run_all(
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={},  # registry empty
            store=store,
        )

        failure = result.failed_models[_MODEL_ID_A]
        assert isinstance(failure, rsf_module.AssignmentFailure)
        assert failure.cause is rsf_module.AssignmentFailureCause.MODEL_NOT_FOUND

    def test_unexpected_exception_in_lower_priority_assignment_is_backstopped(
        self,
    ) -> None:
        """D3/D6 (blocker-requested, RUNNER level): an unanticipated exception
        raised OUTSIDE ``_run_single_model``'s guarded regions, in a
        lower-priority assignment, must not discard an already-succeeded
        higher-priority primary. Against pre-D3 code this exception escapes
        ``run_all_station_forecasts`` entirely (this test fails, not just its
        assertions) — after D3 it is caught and recorded assignment-locally.
        """
        inner_store = FakeModelArtifactStore()
        _seed_artifact(inner_store, _MODEL_ID_A)
        _seed_artifact(inner_store, _MODEL_ID_B)
        store = _RaisingForModelArtifactStore(inner_store, raise_for=_MODEL_ID_B)

        with capture_logs() as logs:
            result = _run_all(
                assignments=[
                    _make_assignment(_MODEL_ID_A, priority=1),
                    _make_assignment(_MODEL_ID_B, priority=2),
                ],
                models={
                    _MODEL_ID_A: FakeStationForecastModel(),
                    _MODEL_ID_B: FakeStationForecastModel(),
                },
                store=store,  # type: ignore[arg-type]
            )

        assert result.primary_model_id == _MODEL_ID_A
        assert _MODEL_ID_A in result.results
        failure = result.failed_models[_MODEL_ID_B]
        assert isinstance(failure, rsf_module.AssignmentFailure)
        assert failure.cause is rsf_module.AssignmentFailureCause.UNEXPECTED_EXCEPTION
        assert any(
            e.get("event") == "run_station_forecast.unexpected_exception"
            and e.get("log_level") == "error"
            for e in logs
        )


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
            model_state_store=FakeModelStateStore(),
        )

    def test_short_coverage_skips_nwp_model_and_native_fallback_wins(self) -> None:
        # future_dynamic has 1 daily row; the NWP model needs 5 → skipped.
        result = self._run_all(future_rows=1)

        assert self._NWP_ID not in result.results
        assert self._NWP_ID in result.failed_models
        failure = result.failed_models[self._NWP_ID]
        assert failure.cause is rsf_module.AssignmentFailureCause.INSUFFICIENT_COVERAGE
        assert "insufficient NWP coverage" in failure.detail
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
            model_state_store=FakeModelStateStore(),
        )

        assert self._NWP_ID not in result.results
        assert self._NWP_ID in result.failed_models
        failure = result.failed_models[self._NWP_ID]
        assert failure.cause is rsf_module.AssignmentFailureCause.INSUFFICIENT_COVERAGE
        assert "insufficient NWP coverage" in failure.detail
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


class _StatefulSpyModel(FakeStationForecastModel):
    """Records every ``prior_state`` its ``predict`` receives — used to prove
    each assignment reads its OWN warm-up state (Plan 148), not one shared
    representative value.
    """

    def __init__(self) -> None:
        self.seen_prior_states: list[bytes | None] = []

    def predict(
        self,
        artifact: object,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        self.seen_prior_states.append(prior_state)
        return super().predict(artifact, inputs, rng, prior_state=prior_state)


def _ensemble_member_inputs(
    k_members: int = 3, issue_time: UtcDatetime = _NOW
) -> StationModelInputs:
    times = [issue_time + (i + 1) * _STEP for i in range(2)]
    data: dict[str, list] = {"timestamp": times}
    for k in range(k_members):
        data[f"precipitation_{k}"] = [1.0, 1.0]
    future_dynamic = pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    obs_rows = [
        {"timestamp": issue_time - timedelta(hours=i), "value": 10.0} for i in range(10)
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
        issue_time=issue_time,
        forecast_horizon_steps=2,
        time_step=_STEP,
    )


class _EnsembleModeNoFutureFeaturesModel:
    """ensemble_mode=ENSEMBLE, no ``future_dynamic_features`` (coverage gate
    skipped) — used to prove the INPUT-side reject-guard fires assignment-local
    on this assignment's OWN persisted state, before ``predict`` is ever
    reached."""

    artifact_scope = ArtifactScope.STATION
    data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset(),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.ENSEMBLE,
    )

    def train(self, *args: object, **kwargs: object) -> bytes:
        return b"artifact"

    def predict(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "predict must not be reached — input-side reject-guard must fire first"
        )

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


class _StatefulOutputEnsembleModel:
    """ensemble_mode=ENSEMBLE, stateless INPUT (no persisted ``prior_state``),
    but each per-member ``predict`` returns a NON-None ``new_state`` —
    unsupported on the OUTPUT side (``reject_stateful_ensemble_states``)."""

    artifact_scope = ArtifactScope.STATION
    data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precipitation"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=2,
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
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        fd = inputs.data.future_dynamic
        values = fd.select(
            pl.col("timestamp").alias("valid_time"),
            pl.lit(1).cast(pl.Int32).alias("member_id"),
            pl.col("precipitation").cast(pl.Float64).alias("value"),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m³/s",
            time_step=inputs.time_step,
            values=values,
        )
        return {"discharge": ensemble}, b"per-member-state"

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


class _RaisingModelStateStore:
    """A ``ModelStateStore`` fake whose ``fetch_latest_state`` raises for one
    specific ``model_id`` — used to prove a store-read failure is
    assignment-local (Plan 148 State-load failure semantics)."""

    def __init__(self, raise_for: ModelId) -> None:
        self._raise_for = raise_for
        self._inner = FakeModelStateStore()

    def store_state(
        self,
        station_id: StationId,
        model_id: ModelId,
        issue_time: object,
        state_bytes: bytes,
    ) -> None:
        self._inner.store_state(station_id, model_id, issue_time, state_bytes)  # type: ignore[arg-type]

    def fetch_latest_state(
        self, station_id: StationId, model_id: ModelId
    ) -> tuple[object, bytes] | None:
        if model_id == self._raise_for:
            raise RuntimeError("store connection lost")
        return self._inner.fetch_latest_state(station_id, model_id)


class _FiMappedFailureModel:
    """A non-ensemble model whose ``predict`` raises ``ModelOutputError`` —
    simulating the FI adapter's ``ModelFailure`` -> ``ModelOutputError``
    mapping (``adapters/forecast_interface.py:370-373``). Proves the
    reject-guard ``try`` (catching ``ModelOutputError`` ONLY at the guard call
    sites) never mislabels this as ``unsupported_stateful_ensemble``."""

    artifact_scope = ArtifactScope.STATION
    data_requirements = FakeStationForecastModel.data_requirements

    def train(self, *args: object, **kwargs: object) -> bytes:
        return b"artifact"

    def predict(self, *args: object, **kwargs: object) -> None:
        raise ModelOutputError("FI ModelFailure mapped to ModelOutputError")

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


class TestPerAssignmentWarmUpState:
    """Plan 148 T2 — red-first bug-demonstrating tests: each model assignment
    reads warm-up state per ``(station_id, model_id)``, and a state-load or
    reject-guard failure is assignment-local (never a station-abort).
    """

    def test_two_stateful_assignments_each_run_with_own_state(self) -> None:
        # THE latent bug this plan fixes: pre-fix, both assignments received
        # the SAME shared ``input_metadata.prior_state`` (the representative
        # model's). Post-fix, each reads its OWN state.
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        model_a = _StatefulSpyModel()
        model_b = _StatefulSpyModel()

        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_A, _NOW - timedelta(hours=1), b"state-a"
        )
        state_store.store_state(
            _STATION_ID, _MODEL_ID_B, _NOW - timedelta(hours=1), b"state-b"
        )

        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={_MODEL_ID_A: model_a, _MODEL_ID_B: model_b},  # type: ignore[dict-item]
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
            model_state_store=state_store,
        )

        assert _MODEL_ID_A in result.results
        assert _MODEL_ID_B in result.results
        assert model_a.seen_prior_states == [b"state-a"]
        assert model_b.seen_prior_states == [b"state-b"]

    def test_ensemble_input_side_reject_guard_is_assignment_local(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_B, _NOW - timedelta(hours=1), b"secondary-state"
        )

        with capture_logs() as logs:
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
                    _MODEL_ID_B: _EnsembleModeNoFutureFeaturesModel(),  # type: ignore[dict-item]
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
                model_state_store=state_store,
            )

        # The primary is STILL returned — the guard did not abort the station.
        assert result is not None
        assert result.model_id == _MODEL_ID_A
        assert any(
            e.get("event") == "run_station_forecast.unsupported_stateful_ensemble"
            for e in logs
        )

    def test_ensemble_output_side_reject_guard_is_assignment_local(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        with capture_logs() as logs:
            result = run_all_station_forecasts(
                station_id=_STATION_ID,
                inputs=_ensemble_member_inputs(),
                input_metadata=_make_metadata(),
                assignments=[
                    _make_assignment(_MODEL_ID_A, priority=1),
                    _make_assignment(_MODEL_ID_B, priority=2),
                ],
                models={
                    _MODEL_ID_A: FakeStationForecastModel(),  # type: ignore[dict-item]
                    _MODEL_ID_B: _StatefulOutputEnsembleModel(),  # type: ignore[dict-item]
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
                model_state_store=FakeModelStateStore(),
            )

        assert result.primary_model_id == _MODEL_ID_A
        assert _MODEL_ID_A in result.results
        assert _MODEL_ID_B in result.failed_models
        assert (
            result.failed_models[_MODEL_ID_B].cause
            is rsf_module.AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE
        )
        assert any(
            e.get("event") == "run_station_forecast.unsupported_stateful_ensemble"
            for e in logs
        )

    def test_ensemble_input_side_reject_guard_via_run_all_returns_typed_failure(
        self,
    ) -> None:
        """Red-first (T1 item 5b): the INPUT-side reject guard's failure is
        exercised via ``run_all_station_forecasts`` directly (not just the
        ``None``-returning wrapper), proving it round-trips a typed
        ``AssignmentFailure`` into ``failed_models`` too.
        """
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_B, _NOW - timedelta(hours=1), b"secondary-state"
        )

        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),  # type: ignore[dict-item]
                _MODEL_ID_B: _EnsembleModeNoFutureFeaturesModel(),  # type: ignore[dict-item]
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
            model_state_store=state_store,
        )

        assert result.primary_model_id == _MODEL_ID_A
        assert _MODEL_ID_B in result.failed_models
        assert (
            result.failed_models[_MODEL_ID_B].cause
            is rsf_module.AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE
        )

    def test_assignment_local_read_failure_keeps_primary(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        state_store = _RaisingModelStateStore(raise_for=_MODEL_ID_B)

        with capture_logs() as logs:
            result = run_all_station_forecasts(
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
                model_state_store=state_store,  # type: ignore[arg-type]
            )

        assert result.primary_model_id == _MODEL_ID_A
        assert _MODEL_ID_A in result.results
        assert _MODEL_ID_B in result.failed_models
        failure = result.failed_models[_MODEL_ID_B]
        assert failure.cause is rsf_module.AssignmentFailureCause.WARM_UP_LOAD_FAILED
        assert "store connection lost" in failure.detail
        assert any(
            e.get("event") == "run_station_forecast.warm_up_load_failed" for e in logs
        )

    def test_predict_raising_model_output_error_is_predict_failed(self) -> None:
        # FI-failure-vs-reject-guard separation: the SAME exception class
        # (``ModelOutputError``) raised from INSIDE ``predict`` must still be
        # caught by the existing ``predict`` boundary as ``predict_failed`` —
        # never mislabeled as a reject-guard event.
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        with capture_logs() as logs:
            result = run_all_station_forecasts(
                station_id=_STATION_ID,
                inputs=_make_inputs(),
                input_metadata=_make_metadata(),
                assignments=[_make_assignment(_MODEL_ID_A, priority=1)],
                models={_MODEL_ID_A: _FiMappedFailureModel()},  # type: ignore[dict-item]
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
                model_state_store=FakeModelStateStore(),
            )

        assert _MODEL_ID_A in result.failed_models
        failure = result.failed_models[_MODEL_ID_A]
        assert failure.cause is rsf_module.AssignmentFailureCause.PREDICT_FAILED
        assert "predict failed" in failure.detail
        events = {e.get("event") for e in logs}
        assert "run_station_forecast.predict_failed" in events
        assert "run_station_forecast.unsupported_stateful_ensemble" not in events
        assert "run_station_forecast.warm_up_load_failed" not in events


class TestPerCauseFallbackAdvancesChain:
    """Plan 150 T1 item 7 (red-first, parametrized): a station whose PRIMARY
    (priority=1) assignment fails with each of the seven anticipated causes in
    turn still produces a successful forecast from the next-priority (B)
    assignment, and ``failed_models[_MODEL_ID_A].cause`` is exactly the
    expected variant. ``UNSUPPORTED_STATEFUL_ENSEMBLE`` uses one guard here —
    both of that cause's return sites are proven independently elsewhere
    (``TestPerAssignmentWarmUpState``); the loop backstop's
    ``UNEXPECTED_EXCEPTION`` is proven in ``TestAssignmentOutcomeShape``.
    """

    def _assert_fallback_advances(
        self,
        *,
        models: dict,
        store: object,
        state_store: object,
        inputs: StationModelInputs,
        expected_cause: object,
    ) -> None:
        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=inputs,
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models=models,  # type: ignore[arg-type]
            artifact_store=store,  # type: ignore[arg-type]
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
            model_state_store=state_store,  # type: ignore[arg-type]
        )

        assert result.primary_model_id == _MODEL_ID_B
        assert _MODEL_ID_B in result.results
        failure = result.failed_models[_MODEL_ID_A]
        assert isinstance(failure, rsf_module.AssignmentFailure)
        assert failure.cause is expected_cause

    def test_model_not_found_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_B)
        self._assert_fallback_advances(
            models={_MODEL_ID_B: FakeStationForecastModel()},
            store=store,
            state_store=FakeModelStateStore(),
            inputs=_make_inputs(),
            expected_cause=rsf_module.AssignmentFailureCause.MODEL_NOT_FOUND,
        )

    def test_insufficient_coverage_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)
        self._assert_fallback_advances(
            models={
                _MODEL_ID_A: _ShortHorizonNwpModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
            state_store=FakeModelStateStore(),
            inputs=_short_nwp_inputs(future_rows=1),
            expected_cause=rsf_module.AssignmentFailureCause.INSUFFICIENT_COVERAGE,
        )

    def test_no_artifact_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_B)  # A's artifact is NOT seeded
        self._assert_fallback_advances(
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
            state_store=FakeModelStateStore(),
            inputs=_make_inputs(),
            expected_cause=rsf_module.AssignmentFailureCause.NO_ARTIFACT,
        )

    def test_warm_up_load_failed_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)
        self._assert_fallback_advances(
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
            state_store=_RaisingModelStateStore(raise_for=_MODEL_ID_A),
            inputs=_make_inputs(),
            expected_cause=rsf_module.AssignmentFailureCause.WARM_UP_LOAD_FAILED,
        )

    def test_unsupported_stateful_ensemble_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)
        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_A, _NOW - timedelta(hours=1), b"primary-state"
        )
        self._assert_fallback_advances(
            models={
                _MODEL_ID_A: _EnsembleModeNoFutureFeaturesModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
            state_store=state_store,
            inputs=_make_inputs(),
            expected_cause=(
                rsf_module.AssignmentFailureCause.UNSUPPORTED_STATEFUL_ENSEMBLE
            ),
        )

    def test_predict_failed_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        class _CrashingModel:
            artifact_scope = FakeStationForecastModel.artifact_scope
            data_requirements = FakeStationForecastModel.data_requirements

            def predict(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("model crashed")

            def serialize_artifact(self, artifact: object) -> bytes:
                return b""

            def deserialize_artifact(self, raw: bytes) -> object:
                return raw

        self._assert_fallback_advances(
            models={
                _MODEL_ID_A: _CrashingModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
            },
            store=store,
            state_store=FakeModelStateStore(),
            inputs=_make_inputs(),
            expected_cause=rsf_module.AssignmentFailureCause.PREDICT_FAILED,
        )

    def test_qc_failed_falls_through(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

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

        result = run_all_station_forecasts(
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
            qc_checker=_FirstFailThenPassChecker(),  # type: ignore[arg-type]
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines=[],
            nwp_cycle_reference_time=_NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=_fixed_clock(),  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=random.Random(42),
            model_state_store=FakeModelStateStore(),
        )

        assert result.primary_model_id == _MODEL_ID_B
        failure = result.failed_models[_MODEL_ID_A]
        assert isinstance(failure, rsf_module.AssignmentFailure)
        assert failure.cause is rsf_module.AssignmentFailureCause.QC_FAILED


class TestGoldenWarmUpProvenance:
    """Plan 148 T3 (b)/(e)/(f) — golden/regression tests at the
    ``run_all_station_forecasts`` level pinning per-assignment warm-up
    provenance.
    """

    def test_empty_store_every_assignment_reports_cold_start(self) -> None:
        # T3(b): multi-model STATELESS station, EMPTY model_state_store
        # (current-production shape) -> every assignment reports COLD_START,
        # both before and after this plan.
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
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
            model_state_store=FakeModelStateStore(),
        )

        for mid in (_MODEL_ID_A, _MODEL_ID_B):
            fc = result.results[mid].forecasts[0]
            assert fc.warm_up_source == WarmUpSource.COLD_START
            assert fc.warm_up_state_age_hours is None

    def test_representative_only_state_secondary_reports_own_cold_start(
        self,
    ) -> None:
        # T3(e), red-first fixture: only the REPRESENTATIVE/primary (model A)
        # has stored state; the lower-priority secondary (model B) has NONE.
        # Post-fix the secondary reports its OWN COLD_START — not the
        # primary's FRESH (today's bug: it would silently inherit A's).
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_A, _NOW - timedelta(hours=1), b"primary-state"
        )
        # No state stored for _MODEL_ID_B.

        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
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
            model_state_store=state_store,
        )

        fc_a = result.results[_MODEL_ID_A].forecasts[0]
        fc_b = result.results[_MODEL_ID_B].forecasts[0]
        assert fc_a.warm_up_source == WarmUpSource.FRESH
        assert fc_b.warm_up_source == WarmUpSource.COLD_START
        assert fc_b.warm_up_state_age_hours is None

    def test_secondary_own_snapshot_diverges_from_primary(self) -> None:
        # T3(f): BOTH assignments have their OWN stored snapshot, at
        # DIFFERENT ages -> each reports its own provenance, not the
        # primary's for both.
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        _seed_artifact(store, _MODEL_ID_B)

        state_store = FakeModelStateStore()
        state_store.store_state(
            _STATION_ID, _MODEL_ID_A, _NOW - timedelta(hours=30), b"primary-snapshot"
        )
        state_store.store_state(
            _STATION_ID, _MODEL_ID_B, _NOW - timedelta(hours=2), b"secondary-fresh"
        )

        result = run_all_station_forecasts(
            station_id=_STATION_ID,
            inputs=_make_inputs(),
            input_metadata=_make_metadata(),
            assignments=[
                _make_assignment(_MODEL_ID_A, priority=1),
                _make_assignment(_MODEL_ID_B, priority=2),
            ],
            models={
                _MODEL_ID_A: FakeStationForecastModel(),
                _MODEL_ID_B: FakeStationForecastModel(),
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
            model_state_store=state_store,
        )

        fc_a = result.results[_MODEL_ID_A].forecasts[0]
        fc_b = result.results[_MODEL_ID_B].forecasts[0]
        # Primary's own snapshot is old -> SNAPSHOT.
        assert fc_a.warm_up_source == WarmUpSource.SNAPSHOT
        assert fc_a.warm_up_state_age_hours is not None
        assert fc_a.warm_up_state_age_hours >= 24.0
        # Secondary's own snapshot is recent -> FRESH — diverges from primary.
        assert fc_b.warm_up_source == WarmUpSource.FRESH
        assert fc_b.warm_up_state_age_hours is not None
        assert fc_b.warm_up_state_age_hours < 24.0


class TestClockSensitiveGolden:
    """Plan 148 D4 — a fixed clock (``_fixed_clock``, used everywhere else in
    this file) returns the SAME instant no matter how many times it is
    called, so it cannot detect an extra ``clock()`` call sneaking into the
    runner, nor a ``created_at``/``updated_at`` that drifted off the ONE call
    D4 promises. A ticking/spy clock can. Single-model, EMPTY state store
    (today's only live-production shape) — golden result plus an exact call
    count on the clock.
    """

    def test_single_model_empty_state_store_exactly_one_clock_call(self) -> None:
        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)
        clock, calls = _ticking_clock(_NOW, timedelta(seconds=1))

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
            clock=clock,  # type: ignore[arg-type]
            id_gen=_sequential_id_gen(),  # type: ignore[arg-type]
            rng=random.Random(42),
            model_state_store=FakeModelStateStore(),
        )

        assert result is not None
        # D2/D4: an empty state store returns COLD_START WITHOUT consulting
        # clock() at all — so the ONLY clock() call left is the runner's own
        # ``now = clock()`` for created_at/updated_at (run_station_forecast.py).
        assert len(calls) == 1
        assert result.forecasts
        for forecast in result.forecasts:
            assert forecast.created_at == calls[0]
            assert forecast.updated_at == calls[0]
        assert result.forecasts[0].warm_up_source == WarmUpSource.COLD_START
        assert result.forecasts[0].warm_up_state_age_hours is None


class _EnsembleValueSpyModel:
    """Stateless ensemble model that echoes the ``precipitation`` column
    straight into the forecast value, so a test can prove WHICH
    ``StationModelInputs`` object the fan-out actually sliced."""

    artifact_scope = ArtifactScope.STATION
    data_requirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precipitation"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=2,
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
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        fd = inputs.data.future_dynamic
        values = fd.select(
            pl.col("timestamp").alias("valid_time"),
            pl.lit(1).cast(pl.Int32).alias("member_id"),
            pl.col("precipitation").cast(pl.Float64).alias("value"),
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter="discharge",
            units="m³/s",
            time_step=inputs.time_step,
            values=values,
        )
        return {"discharge": ensemble}, None

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


class TestContextInputsIsTheSingleInputAuthority:
    """Review fix (Plan 148 diff review, major finding) — ``predict``,
    ensemble fan-out, and ``issued_at`` must read ``ModelRunContext.inputs``,
    never the raw ``inputs`` argument. Phase 1 keeps ``context.inputs is
    inputs`` for every real caller (per-assignment inputs is a later phase),
    so an end-to-end run with matching objects can't distinguish the two
    authorities. These tests inject a DISTINGUISHABLE ``context.inputs`` (a
    spy wrapping ``ModelRunContext`` construction) that differs from the raw
    param, and assert every consumer follows ``context.inputs`` — closing the
    "two competing input authorities" finding structurally, not just by
    inspection.
    """

    def test_non_ensemble_predict_and_issued_at_use_context_inputs(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        import sapphire_flow.services.run_station_forecast as rsf_module

        raw_inputs = _make_inputs(issue_time=_NOW)
        context_inputs = _make_inputs(issue_time=_NOW + timedelta(hours=7))
        assert context_inputs.issue_time != raw_inputs.issue_time

        real_context_cls = rsf_module.ModelRunContext

        def spy_context(**kwargs: object) -> object:
            kwargs["inputs"] = context_inputs
            return real_context_cls(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(rsf_module, "ModelRunContext", spy_context)

        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        class _InputSpyModel(FakeStationForecastModel):
            def __init__(self) -> None:
                self.seen_inputs: StationModelInputs | None = None

            def predict(
                self,
                artifact: object,
                inputs: StationModelInputs,
                rng: random.Random,
                prior_state: bytes | None = None,
            ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
                self.seen_inputs = inputs
                return super().predict(artifact, inputs, rng, prior_state=prior_state)

        spy_model = _InputSpyModel()

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=raw_inputs,
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: spy_model},  # type: ignore[dict-item]
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
            model_state_store=FakeModelStateStore(),
        )

        assert result is not None
        # predict() must receive context.inputs, NOT the raw `inputs` param.
        assert spy_model.seen_inputs is context_inputs
        # issued_at must be stamped from context.inputs, NOT the raw param.
        assert result.forecasts[0].issued_at == context_inputs.issue_time
        assert result.forecasts[0].issued_at != raw_inputs.issue_time

    def test_ensemble_fan_out_uses_context_inputs(
        self, monkeypatch: MonkeyPatch
    ) -> None:
        import sapphire_flow.services.run_station_forecast as rsf_module

        raw_inputs = _ensemble_member_inputs(k_members=2)
        distinguishable_future_dynamic = raw_inputs.data.future_dynamic.with_columns(
            pl.lit(77.0).alias("precipitation_0"),
            pl.lit(77.0).alias("precipitation_1"),
        )
        context_inputs = dataclasses.replace(
            raw_inputs,
            data=dataclasses.replace(
                raw_inputs.data, future_dynamic=distinguishable_future_dynamic
            ),
        )

        real_context_cls = rsf_module.ModelRunContext

        def spy_context(**kwargs: object) -> object:
            kwargs["inputs"] = context_inputs
            return real_context_cls(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(rsf_module, "ModelRunContext", spy_context)

        store = FakeModelArtifactStore()
        _seed_artifact(store, _MODEL_ID_A)

        result = run_station_forecast(
            station_id=_STATION_ID,
            inputs=raw_inputs,
            input_metadata=_make_metadata(),
            assignments=[_make_assignment(_MODEL_ID_A)],
            models={_MODEL_ID_A: _EnsembleValueSpyModel()},  # type: ignore[dict-item]
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
            model_state_store=FakeModelStateStore(),
        )

        assert result is not None
        # `_EnsembleValueSpyModel.predict` echoes the future_dynamic
        # "precipitation" column verbatim as the ensemble value: if the
        # fan-out consumed the RAW `inputs` param (value 1.0), every member
        # value would be 1.0; consuming `context.inputs` yields 77.0.
        values = result.forecasts[0].ensemble.values["value"].to_list()
        assert values
        assert all(v == 77.0 for v in values)
