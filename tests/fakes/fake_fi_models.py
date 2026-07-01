from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from sapphire_flow.adapters import forecast_interface as fi_boundary

REFERENCE_FI_STEP = timedelta(hours=1)
REFERENCE_FI_HORIZON = 3
REFERENCE_FI_DEFAULT_MEMBERS = 8
REFERENCE_FI_QUANTILE_LEVELS = (0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.95)


@dataclass(frozen=True, slots=True)
class ReferenceFIArtifact:
    bias: float


class ReferenceFIForecastModel:
    def __init__(
        self,
        artifact_scope: fi_boundary.FIArtifactScope = (
            fi_boundary.FIArtifactScope.STATION
        ),
        *,
        member_count: int | None = None,
        quantile_levels: tuple[float, ...] | None = None,
        deterministic: bool = False,
        use_global_random: bool = False,
        model_failure: bool = False,
        all_variables_fail: bool = False,
    ) -> None:
        output_mode_count = sum(
            (
                member_count is not None,
                quantile_levels is not None,
                deterministic,
            )
        )
        if output_mode_count > 1:
            raise ValueError(
                "ReferenceFIForecastModel accepts only one output representation"
            )
        if output_mode_count == 0:
            member_count = REFERENCE_FI_DEFAULT_MEMBERS

        self.artifact_scope = artifact_scope
        self._input_requirement = reference_fi_input_requirement()
        self._member_count = member_count
        self._quantile_levels = quantile_levels
        self._deterministic = deterministic
        self._use_global_random = use_global_random
        self._model_failure = model_failure
        self._all_variables_fail = all_variables_fail

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return self._input_requirement

    def train(
        self,
        inputs: fi_boundary.ModelInputs,
        *,
        config: object,
        rng: random.Random,
    ) -> ReferenceFIArtifact:
        del inputs, config
        if self._use_global_random:
            return ReferenceFIArtifact(bias=random.random())
        return ReferenceFIArtifact(bias=rng.gauss(10.0, 0.25))

    def predict(
        self,
        artifact: ReferenceFIArtifact,
        *,
        inputs: fi_boundary.ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi_boundary.ModelResult:
        if self._model_failure:
            return fi_boundary.ModelFailure(
                model_name="reference-fi",
                issue_datetime=issue_datetime,
                cause=fi_boundary.FailureCause.MODEL_ERROR,
                message="boom",
            )

        variables = {
            station_key: {
                "discharge": self._variable_output(
                    artifact=artifact,
                    station_key=station_key,
                    issue_datetime=issue_datetime,
                    rng=rng,
                )
            }
            for station_key in inputs.stations
        }
        return fi_boundary.ModelSuccess(
            output=fi_boundary.ModelOutput(
                model_name="reference-fi",
                issue_datetime=issue_datetime,
                variables=variables,
            )
        )

    def serialize_artifact(self, artifact: ReferenceFIArtifact) -> bytes:
        return f"{artifact.bias:.17g}".encode("ascii")

    def deserialize_artifact(self, raw: bytes) -> ReferenceFIArtifact:
        return ReferenceFIArtifact(bias=float(raw.decode("ascii")))

    def _variable_output(
        self,
        *,
        artifact: ReferenceFIArtifact,
        station_key: str,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi_boundary.VariableOutput:
        metadata = fi_boundary.VariableMetadata(
            unit=fi_boundary.Unit.M3_PER_S,
            timedelta=REFERENCE_FI_STEP,
            forecast_horizon=REFERENCE_FI_HORIZON,
            offset=0,
        )
        if self._all_variables_fail:
            return fi_boundary.VariableOutput(
                metadata=metadata,
                flags=frozenset(),
                status=fi_boundary.VariableStatus.FAILURE,
            )
        if self._deterministic:
            return fi_boundary.VariableOutput(
                metadata=metadata,
                deterministic=fi_boundary.DeterministicData(
                    data=_deterministic_frame(
                        artifact=artifact,
                        station_key=station_key,
                        issue_datetime=issue_datetime,
                        rng=rng,
                    )
                ),
                flags=frozenset(),
                status=fi_boundary.VariableStatus.SUCCESS,
            )
        if self._quantile_levels is not None:
            return fi_boundary.VariableOutput(
                metadata=metadata,
                quantiles=fi_boundary.QuantileData(
                    quantile_levels=list(self._quantile_levels),
                    data=_quantile_frame(
                        artifact=artifact,
                        station_key=station_key,
                        issue_datetime=issue_datetime,
                        quantile_levels=self._quantile_levels,
                        rng=rng,
                    ),
                ),
                flags=frozenset(),
                status=fi_boundary.VariableStatus.SUCCESS,
            )

        if self._member_count is None:
            raise AssertionError("member_count is required for trajectory output")
        return fi_boundary.VariableOutput(
            metadata=metadata,
            trajectories=fi_boundary.TrajectoryData(
                num_samples=self._member_count,
                data=_trajectory_frame(
                    artifact=artifact,
                    station_key=station_key,
                    issue_datetime=issue_datetime,
                    member_count=self._member_count,
                    rng=rng,
                ),
            ),
            flags=frozenset(),
            status=fi_boundary.VariableStatus.SUCCESS,
        )


def reference_fi_input_requirement() -> fi_boundary.InputRequirement:
    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            REFERENCE_FI_STEP: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": fi_boundary.PastKnownVariable(
                                        lookback=3,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.M3_PER_S,
                                    )
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation_forecast": (
                                        fi_boundary.FutureKnownVariable(
                                            future_steps=REFERENCE_FI_HORIZON,
                                            max_nan=0,
                                            unit=fi_boundary.Unit.MM,
                                        )
                                    )
                                }
                            },
                        )
                    )
                }
            )
        },
    )


M2_FI_STEP = timedelta(hours=24)
M2_FI_HORIZON = 5


def m2_fi_input_requirement() -> fi_boundary.InputRequirement:
    """M2 scenario requirement.

    discharge is BOTH a ``target`` and its own ``past_known`` history;
    precipitation + temperature are ``future_known`` forcing. This exercises the
    projection contract that target history must NOT leak into
    ``past_dynamic_features``.
    """
    return fi_boundary.InputRequirement(
        targets={
            "discharge": fi_boundary.TargetSpec(
                unit=fi_boundary.Unit.M3_PER_S,
                representations=frozenset(
                    {fi_boundary.OutputRepresentation.DETERMINISTIC}
                ),
            )
        },
        dynamic={
            M2_FI_STEP: fi_boundary.SpatialInputSpec(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": fi_boundary.PastKnownVariable(
                                        lookback=7,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.M3_PER_S,
                                    )
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation": fi_boundary.FutureKnownVariable(
                                        future_steps=M2_FI_HORIZON,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.MM,
                                    ),
                                    "temperature": fi_boundary.FutureKnownVariable(
                                        future_steps=M2_FI_HORIZON,
                                        max_nan=0,
                                        unit=fi_boundary.Unit.DEG_C,
                                    ),
                                }
                            },
                        )
                    )
                }
            )
        },
    )


def _station_offset(station_key: str) -> float:
    return float(sum(ord(char) for char in station_key) % 100)


def _valid_times(issue_datetime: datetime) -> list[datetime]:
    return [
        issue_datetime + (step + 1) * REFERENCE_FI_STEP
        for step in range(REFERENCE_FI_HORIZON)
    ]


def _base_frame(issue_datetime: datetime) -> dict[str, list[object]]:
    return {
        "issue_datetime": [issue_datetime] * REFERENCE_FI_HORIZON,
        "datetime": _valid_times(issue_datetime),
    }


def _cast_output_frame(data: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(data).with_columns(
        pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
        pl.col("datetime").cast(pl.Datetime("us", "UTC")),
    )


def _trajectory_frame(
    *,
    artifact: ReferenceFIArtifact,
    station_key: str,
    issue_datetime: datetime,
    member_count: int,
    rng: random.Random,
) -> pl.DataFrame:
    station_offset = _station_offset(station_key)
    data = _base_frame(issue_datetime)
    for member_id in range(1, member_count + 1):
        data[str(member_id)] = [
            artifact.bias
            + station_offset
            + float(step)
            + (member_id / 100.0)
            + rng.gauss(0.0, 0.001)
            for step in range(REFERENCE_FI_HORIZON)
        ]
    return _cast_output_frame(data)


def _quantile_frame(
    *,
    artifact: ReferenceFIArtifact,
    station_key: str,
    issue_datetime: datetime,
    quantile_levels: tuple[float, ...],
    rng: random.Random,
) -> pl.DataFrame:
    station_offset = _station_offset(station_key)
    data = _base_frame(issue_datetime)
    for level in quantile_levels:
        data[str(level)] = [
            artifact.bias + station_offset + float(step) + level + rng.gauss(0.0, 0.001)
            for step in range(REFERENCE_FI_HORIZON)
        ]
    return _cast_output_frame(data)


def _deterministic_frame(
    *,
    artifact: ReferenceFIArtifact,
    station_key: str,
    issue_datetime: datetime,
    rng: random.Random,
) -> pl.DataFrame:
    station_offset = _station_offset(station_key)
    data = _base_frame(issue_datetime)
    data["value"] = [
        artifact.bias + station_offset + float(step) + rng.gauss(0.0, 0.001)
        for step in range(REFERENCE_FI_HORIZON)
    ]
    return _cast_output_frame(data)
