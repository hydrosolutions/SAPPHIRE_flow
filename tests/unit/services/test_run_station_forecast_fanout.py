"""LOCKED tests: operational ``_run_single_model`` fans out ensemble-mode models.

A STATION model whose ``data_requirements.ensemble_mode == ENSEMBLE`` fed a
member-suffixed ``future_dynamic`` (``precipitation_0..20`` / ``temperature_0..20``)
must be fanned out into a 21-member ensemble. A single-mode model is left
untouched: predict is called once, receiving the raw suffixed columns.

RED reason (pre-implementation): ``EnsembleMode`` / the ``ensemble_mode`` field do
not exist (collection error); and ``_run_single_model`` calls ``model.predict``
directly instead of fanning out.
"""

from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.run_station_forecast import (
    OperationalInputMetadata,
    run_station_forecast,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleMode,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    SpatialRepresentation,
    WarmUpSource,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.model import (
    ModelArtifact,
    ModelDataRequirements,
    ModelParams,
    StationInputData,
    StationModelInputs,
    StationTrainingData,
)
from sapphire_flow.types.station import ModelAssignment
from tests.fakes.fake_stores import FakeModelArtifactStore

_NOW = ensure_utc(datetime(2025, 6, 1, 6, 0, tzinfo=UTC))
_STEP = timedelta(days=1)
_STATION_ID = StationId(uuid4())
_MODEL_ID = ModelId("ensemble-model")
_HORIZON = 2


def _requirements(ensemble_mode: EnsembleMode) -> ModelDataRequirements:
    return ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset(),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({_STEP}),
        lookback_steps=1,
        forecast_horizon_steps=_HORIZON,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        ensemble_mode=ensemble_mode,
    )


class _EnsembleModeStationModel:
    """ensemble_mode=ENSEMBLE; each predict emits a 1-member ensemble keyed on the
    bare ``precipitation`` column that only exists after a per-member slice."""

    def __init__(self) -> None:
        from sapphire_flow.types.enums import ArtifactScope

        self.artifact_scope = ArtifactScope.STATION
        self.data_requirements = _requirements(EnsembleMode.ENSEMBLE)

    def train(
        self, data: StationTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        return b"artifact"

    def predict(
        self,
        artifact: ModelArtifact,
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

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"artifact"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


class _StatefulEnsembleModel(_EnsembleModeStationModel):
    """ensemble_mode=ENSEMBLE, but each per-member predict returns a NON-None
    warm-up state. Combining N per-member states is unsupported, so the fan-out
    path must fail loudly instead of silently dropping the state."""

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        ensembles, _ = super().predict(artifact, inputs, rng, prior_state)
        return ensembles, b"per-member-state"


class _PriorStateSpyEnsembleModel(_EnsembleModeStationModel):
    """ensemble_mode=ENSEMBLE; records every ``prior_state`` its per-member predict
    receives so a test can prove the aggregate input state is never forwarded."""

    def __init__(self) -> None:
        super().__init__()
        self.seen_prior_states: list[bytes | None] = []

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        self.seen_prior_states.append(prior_state)
        return super().predict(artifact, inputs, rng, prior_state)


class _SingleModeStationModel:
    """ensemble_mode=SINGLE; predict is called ONCE with the raw suffixed columns
    (records them) and returns its own fixed 4-member ensemble — no fan-out."""

    def __init__(self) -> None:
        from sapphire_flow.types.enums import ArtifactScope

        self.artifact_scope = ArtifactScope.STATION
        self.data_requirements = _requirements(EnsembleMode.SINGLE)
        self.received_columns: list[str] = []
        self.predict_calls = 0

    def train(
        self, data: StationTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        return b"artifact"

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        self.predict_calls += 1
        self.received_columns = list(inputs.data.future_dynamic.columns)
        rows = []
        for step in range(_HORIZON):
            vt = ensure_utc(_NOW + (step + 1) * _STEP)
            for member in range(4):
                rows.append({"valid_time": vt, "member_id": member, "value": 5.0})
        values = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
            pl.col("value").cast(pl.Float64),
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

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"artifact"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


def _member_suffixed_inputs(k_members: int) -> StationModelInputs:
    times = [ensure_utc(_NOW + (i + 1) * _STEP) for i in range(_HORIZON)]
    data: dict[str, list] = {"timestamp": times}
    for k in range(k_members):
        data[f"precipitation_{k}"] = [1.0 + k] * _HORIZON
        data[f"temperature_{k}"] = [10.0 + k] * _HORIZON
    future_dynamic = pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    empty = pl.DataFrame({"timestamp": []}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=empty,
            past_dynamic=empty,
            future_dynamic=future_dynamic,
            static=None,
        ),
        issue_time=_NOW,
        forecast_horizon_steps=_HORIZON,
        time_step=_STEP,
    )


def _metadata() -> OperationalInputMetadata:
    return OperationalInputMetadata(
        warm_up_source=WarmUpSource.FRESH,
        warm_up_state_age_hours=None,
        observation_staleness_hours=1.0,
        prior_state=None,
        nwp_age_hours=0.5,
    )


def _assignment() -> ModelAssignment:
    return ModelAssignment(
        station_id=_STATION_ID,
        model_id=_MODEL_ID,
        time_step=_STEP,
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_NOW,
    )


def _seed_artifact(store: FakeModelArtifactStore) -> ArtifactId:
    aid, _ = store.store_artifact(
        model_id=_MODEL_ID,
        artifact_bytes=b"artifact",
        training_period_start=_NOW,
        training_period_end=_NOW,
        trained_at=_NOW,
        station_id=_STATION_ID,
        status=ModelArtifactStatus.ACTIVE,
    )
    return aid


def _id_gen() -> object:
    ids = [uuid4() for _ in range(50)]
    idx = [0]

    def gen() -> UUID:
        val = ids[idx[0]]
        idx[0] += 1
        return val

    return gen


def _run(model: object, metadata: OperationalInputMetadata | None = None) -> object:
    from tests.conftest import make_deployment_config

    store = FakeModelArtifactStore()
    _seed_artifact(store)
    return run_station_forecast(
        station_id=_STATION_ID,
        inputs=_member_suffixed_inputs(k_members=21),
        input_metadata=metadata if metadata is not None else _metadata(),
        assignments=[_assignment()],
        models={_MODEL_ID: model},  # type: ignore[dict-item]
        artifact_store=store,
        qc_checker=ForecastOutputQualityChecker(),
        qc_rules=ForecastQcRuleSet(version="1.0", rules=()),
        qc_overrides=[],
        baselines=[],
        nwp_cycle_reference_time=_NOW,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        config=make_deployment_config(),
        clock=lambda: _NOW,  # type: ignore[arg-type,return-value]
        id_gen=_id_gen(),  # type: ignore[arg-type]
        rng=random.Random(42),
    )


class TestEnsembleModeFanOut:
    def test_ensemble_mode_model_yields_21_member_forecast(self) -> None:
        result = _run(_EnsembleModeStationModel())

        assert result is not None
        assert result.ensembles["discharge"].member_count == 21

    def test_single_mode_model_is_not_fanned_out(self) -> None:
        model = _SingleModeStationModel()

        result = _run(model)

        assert result is not None
        # No fan-out: the model's own 4-member ensemble is preserved verbatim.
        assert result.ensembles["discharge"].member_count == 4
        # predict was called once with the raw member-suffixed columns intact.
        assert model.predict_calls == 1
        assert "precipitation_0" in model.received_columns


class TestEnsembleFanOutState:
    def test_stateless_ensemble_reports_no_state(self) -> None:
        # M2's real case: every per-member predict returns state ``None`` — the
        # aggregate carries no warm-up state and nothing is lost.
        result = _run(_EnsembleModeStationModel())

        assert result is not None
        assert result.new_state is None
        assert result.ensembles["discharge"].member_count == 21

    def test_stateful_ensemble_fails_loudly(self) -> None:
        # A per-member predict returning a NON-None state is unsupported: combining
        # N per-member states is ill-defined, so the fan-out must raise rather than
        # silently drop the state.
        with pytest.raises(ModelOutputError, match=r"(?i)per-member state|warm-up"):
            _run(_StatefulEnsembleModel())

    def test_prior_state_on_ensemble_path_fails_loudly(self) -> None:
        # INPUT-side complement: an aggregate ``prior_state`` cannot be split per
        # member, so feeding one to an ensemble-mode model is unsupported. The
        # guard must PROPAGATE (raise), not be swallowed into a graceful string,
        # and predict must never see the forwarded aggregate state.
        model = _PriorStateSpyEnsembleModel()
        metadata = dataclasses.replace(_metadata(), prior_state=b"aggregate-state")

        with pytest.raises(ModelOutputError, match=r"(?i)per-member state|warm-up"):
            _run(model, metadata)

        assert model.seen_prior_states == []
