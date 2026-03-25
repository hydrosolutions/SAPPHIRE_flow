from __future__ import annotations

import random  # noqa: TC003
from datetime import UTC, datetime, timedelta

import polars as pl

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
from sapphire_flow.types.ids import StationId  # noqa: TC001
from sapphire_flow.types.model import (  # noqa: TC001
    GroupTrainingData,
    ModelArtifact,
    ModelInputs,
    ModelParams,
    TrainingData,
)


class FakeStationForecastModel:
    artifact_scope = ArtifactScope.STATION
    required_features: frozenset[str] = frozenset({"precipitation", "temperature"})
    required_static_attributes: frozenset[str] = frozenset()
    spatial_input_type = SpatialRepresentation.POINT
    supported_time_steps: frozenset[timedelta] = frozenset(
        {timedelta(hours=1), timedelta(hours=24)}
    )
    parameter: str = "discharge"
    units: str = "m3/s"

    def train(
        self, data: TrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        return b"fake_artifact"

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: ModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[ForecastEnsemble, bytes | None]:
        n_members = 21
        rows = []
        for step in range(inputs.forecast_horizon_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(
                    inputs.issue_time.timestamp()
                    + (step + 1) * inputs.time_step.total_seconds(),
                    tz=UTC,
                )
            )
            for m in range(n_members):
                rows.append(
                    {"valid_time": vt, "member_id": m, "value": rng.uniform(1.0, 50.0)}
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
        return (ens, b"fake_state")

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"serialized"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw


class FakeGroupForecastModel:
    artifact_scope = ArtifactScope.GROUP
    required_features: frozenset[str] = frozenset({"precipitation", "temperature"})
    required_static_attributes: frozenset[str] = frozenset()
    spatial_input_type = SpatialRepresentation.POINT
    supported_time_steps: frozenset[timedelta] = frozenset({timedelta(hours=1)})
    parameter: str = "discharge"
    units: str = "m3/s"

    def train(
        self, data: GroupTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        return b"fake_group_artifact"

    def predict_batch(
        self,
        artifact: ModelArtifact,
        inputs: dict[StationId, ModelInputs],
        rng: random.Random,
    ) -> dict[StationId, tuple[ForecastEnsemble, bytes | None]]:
        result = {}
        for sid, inp in inputs.items():
            rows = []
            for step in range(inp.forecast_horizon_steps):
                vt = ensure_utc(
                    datetime.fromtimestamp(
                        inp.issue_time.timestamp()
                        + (step + 1) * inp.time_step.total_seconds(),
                        tz=UTC,
                    )
                )
                for m in range(21):
                    rows.append(
                        {
                            "valid_time": vt,
                            "member_id": m,
                            "value": rng.uniform(1.0, 50.0),
                        }
                    )
            df = pl.DataFrame(rows).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
                pl.col("member_id").cast(pl.Int32),
            )
            ens = ForecastEnsemble.from_members(
                station_id=sid,
                issued_at=inp.issue_time,
                parameter=self.parameter,
                units=self.units,
                time_step=inp.time_step,
                values=df,
            )
            result[sid] = (ens, None)
        return result

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        return artifact if isinstance(artifact, bytes) else b"serialized"

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        return raw
