"""LOCKED tests: conformance harness fans out ensemble-mode models to the floor.

For an ``ensemble_mode == ENSEMBLE`` model the synthetic conformance harness must
synthesize ``f"{feat}_{k}"`` member columns (k >= min_operational_ensemble_size)
and fan out over them, so ``assert_operational_floors`` observes
``member_count >= 20`` even though the underlying model emits a single
deterministic trajectory per member.

RED reason (pre-implementation): today the harness feeds the ensemble-mode model
bare synthetic forcing → the FI adapter returns a 1-member ensemble → the floor
check raises ``observed_count=1 ... required_floor=20``. The native (single-mode)
path and the fixed-seed determinism guard must remain green.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.adapters.forecast_interface import adapt_if_fi
from sapphire_flow.exceptions import ModelSmokeTestError
from sapphire_flow.models.nwp_regression import NwpRainfallRunoff
from sapphire_flow.services.model_onboarding import (
    assert_model_conforms,
    assert_operational_floors,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ArtifactScope,
    EnsembleMode,
    SpatialRepresentation,
)
from sapphire_flow.types.model import (
    ModelArtifact,
    ModelDataRequirements,
    ModelParams,
    StationModelInputs,
    StationTrainingData,
)
from tests.conftest import make_deployment_config
from tests.fakes.fake_models import FakeStationForecastModel


class StatefulEnsembleStationModel:
    """ENSEMBLE-mode STATION model whose per-member ``predict`` returns a
    non-``None`` warm-up state. Fanning out N such members yields N per-member
    states that cannot be aggregated — onboarding must reject it at the gate,
    consistent with the operational fan-out.
    """

    artifact_scope = ArtifactScope.STATION
    data_requirements: ModelDataRequirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation"}),
        future_dynamic_features=frozenset({"precipitation"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=24)}),
        lookback_steps=10,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.ENSEMBLE,
    )
    parameter: str = "discharge"
    units: str = "m³/s"

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
        rows = []
        for step in range(inputs.forecast_horizon_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(
                    inputs.issue_time.timestamp()
                    + (step + 1) * inputs.time_step.total_seconds(),
                    tz=UTC,
                )
            )
            rows.append(
                {"valid_time": vt, "member_id": 0, "value": rng.uniform(1.0, 50.0)}
            )
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ens = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter=self.parameter,
            units=self.units,
            time_step=inputs.time_step,
            values=df,
        )
        # NON-None per-member state → stateful ensemble model (unsupported).
        return ({self.parameter: ens}, b"member_state")

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"artifact"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


class StrictSchemaEnsembleStationModel:
    """ENSEMBLE-mode STATION model that asserts a BARE training schema.

    Real training/reanalysis data is a single bare trajectory (member_id=None →
    bare ``precipitation``). ``train()`` here RAISES if ``future_dynamic`` carries
    any member-suffixed (``^{feat}_\\d+$``) column — the predict-only fan-out
    shape. The conformance harness must therefore feed train() bare data and build
    the member-suffixed forcing separately for the predict fan-out only.
    """

    artifact_scope = ArtifactScope.STATION
    data_requirements: ModelDataRequirements = ModelDataRequirements(
        target_parameters=frozenset({"discharge"}),
        past_dynamic_features=frozenset({"precipitation"}),
        future_dynamic_features=frozenset({"precipitation"}),
        static_features=frozenset(),
        supported_time_steps=frozenset({timedelta(hours=24)}),
        lookback_steps=10,
        forecast_horizon_steps=5,
        spatial_input_type=SpatialRepresentation.POINT,
        ensemble_mode=EnsembleMode.ENSEMBLE,
    )
    parameter: str = "discharge"
    units: str = "m³/s"

    def train(
        self, data: StationTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        member_suffixed = [
            col
            for col in data.future_dynamic.columns
            for feat in self.data_requirements.future_dynamic_features
            if re.fullmatch(rf"{re.escape(feat)}_\d+", col)
        ]
        if member_suffixed:
            raise ValueError(
                "strict ensemble model requires a bare training schema; "
                f"got member-suffixed columns {member_suffixed}"
            )
        return b"artifact"

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        rows = []
        for step in range(inputs.forecast_horizon_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(
                    inputs.issue_time.timestamp()
                    + (step + 1) * inputs.time_step.total_seconds(),
                    tz=UTC,
                )
            )
            rows.append(
                {"valid_time": vt, "member_id": 0, "value": rng.uniform(1.0, 50.0)}
            )
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        ens = ForecastEnsemble.from_members(
            station_id=inputs.station_id,
            issued_at=inputs.issue_time,
            parameter=self.parameter,
            units=self.units,
            time_step=inputs.time_step,
            values=df,
        )
        return ({self.parameter: ens}, None)

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"artifact"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


def test_ensemble_mode_model_meets_operational_floor() -> None:
    model = adapt_if_fi(NwpRainfallRunoff())
    assert isinstance(model, fi_boundary.ForecastInterfaceAdapter)

    # Must NOT raise: the harness synthesizes >=20 members and fans out.
    assert_operational_floors(model, make_deployment_config(), random.Random(123))


def test_native_single_mode_model_still_onboards() -> None:
    # Native (single-mode) model: no member columns, no fan-out — unchanged.
    assert_operational_floors(
        FakeStationForecastModel(),
        make_deployment_config(),
        random.Random(123),
    )


def test_ensemble_mode_model_conforms_deterministically() -> None:
    model = adapt_if_fi(NwpRainfallRunoff())
    assert isinstance(model, fi_boundary.ForecastInterfaceAdapter)

    # Fixed-seed determinism check still holds for the ensemble-mode model.
    assert_model_conforms(model, random.Random(123))


def test_stateful_ensemble_model_rejected_by_operational_floors() -> None:
    # A stateful ensemble model must fail the gate, not silently pass onboarding
    # and only blow up operationally.
    with pytest.raises(
        ModelSmokeTestError,
        match="stateful ensemble models need per-member state",
    ):
        assert_operational_floors(
            StatefulEnsembleStationModel(),
            make_deployment_config(),
            random.Random(123),
        )


def test_stateful_ensemble_model_rejected_by_conformance() -> None:
    with pytest.raises(
        ModelSmokeTestError,
        match="stateful ensemble models need per-member state",
    ):
        assert_model_conforms(StatefulEnsembleStationModel(), random.Random(123))


def test_strict_schema_ensemble_model_trains_on_bare_data() -> None:
    # REGRESSION: the harness must feed train() a BARE single-trajectory schema
    # and build the member-suffixed forcing SEPARATELY for the predict fan-out.
    # A schema-strict ensemble model (train() rejects member-suffixed columns)
    # therefore onboards cleanly. Under the pre-fix harness train() saw the
    # suffixed columns and this raised (RED).
    model = StrictSchemaEnsembleStationModel()
    assert_operational_floors(model, make_deployment_config(), random.Random(123))
    assert_model_conforms(model, random.Random(123))
