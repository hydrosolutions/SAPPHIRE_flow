from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.types.enums import AlertEligibility, ModelTier

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping

SMOKE_STEP = timedelta(hours=1)
SMOKE_HORIZON = 3
SMOKE_MEMBERS = 20


class _ConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    precipitation_factor: float = Field(gt=0, allow_inf_nan=False)
    member_increment: float = Field(gt=0, allow_inf_nan=False)


class _ArtifactPayload(_ConfigPayload):
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, kw_only=True, slots=True)
class DeploymentSmokeArtifact:
    config_hash: str
    precipitation_factor: float
    member_increment: float

    def __post_init__(self) -> None:
        if len(self.config_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.config_hash
        ):
            raise ValueError("smoke artifact requires a SHA-256 configuration hash")
        if not all(
            math.isfinite(value) and value > 0
            for value in (self.precipitation_factor, self.member_increment)
        ):
            raise ValueError("smoke artifact coefficients must be finite and positive")


class DeploymentSmokeModel:
    artifact_scope = fi.FIArtifactScope.STATION
    model_tier = ModelTier.SKILL
    alert_eligibility = AlertEligibility.NO_EVENT_INFORMATION

    def __init__(self, config_bytes: bytes) -> None:
        parsed = _ConfigPayload.model_validate_json(config_bytes)
        self._artifact = DeploymentSmokeArtifact(
            config_hash=hashlib.sha256(config_bytes).hexdigest(),
            precipitation_factor=parsed.precipitation_factor,
            member_increment=parsed.member_increment,
        )

    @property
    def config_hash(self) -> str:
        return self._artifact.config_hash

    @property
    def input_requirement(self) -> fi.InputRequirement:
        return fi.InputRequirement(
            targets={
                "discharge": fi.TargetSpec(
                    unit=fi.Unit.M3_PER_S,
                    representations=frozenset({fi.OutputRepresentation.TRAJECTORIES}),
                )
            },
            dynamic={
                SMOKE_STEP: fi.SpatialInputSpec(
                    data={
                        fi.FISpatialRepresentation.POINT: fi.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": fi.PastKnownVariable(
                                        lookback=3, max_nan=0, unit=fi.Unit.M3_PER_S
                                    )
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation": fi.FutureKnownVariable(
                                        future_steps=SMOKE_HORIZON,
                                        max_nan=0,
                                        unit=fi.Unit.MM,
                                        ensemble_mode=fi.FIEnsembleMode.SINGLE,
                                    )
                                }
                            },
                        )
                    }
                )
            },
        )

    def train(
        self,
        inputs: fi.ModelInputs,
        *,
        config: Mapping[str, Any],
        rng: random.Random,
    ) -> DeploymentSmokeArtifact:
        # Diagnostic constants are configured, not fitted to scientific data.
        if config:
            raise ValueError("smoke training configuration must be empty")
        return self._artifact

    def predict(
        self,
        artifact: object,
        *,
        inputs: fi.ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi.ModelResult:
        if artifact != self._artifact:
            return _failure(
                issue_datetime,
                "artifact configuration mismatch",
                fi.FailureCause.CONFIGURATION,
            )
        if len(inputs.stations) != 1:
            return _failure(issue_datetime, "exactly one station is required")
        station_key, station = next(iter(inputs.stations.items()))
        future_times = [issue_datetime + n * SMOKE_STEP for n in (1, 2, 3)]
        past_times = [issue_datetime - n * SMOKE_STEP for n in (3, 2, 1)]
        try:
            branch = station.dynamic[SMOKE_STEP].data[fi.FISpatialRepresentation.POINT]
            discharge = _series_values(
                branch.past_known["obs"]["discharge"],
                past_times,
                fi.Unit.M3_PER_S,
                "discharge",
            )
            precipitation = _series_values(
                branch.future_known["nwp"]["precipitation"],
                future_times,
                fi.Unit.MM,
                "precipitation",
            )
        except KeyError:
            return _failure(issue_datetime, "required hourly point inputs are missing")
        except ValueError as exc:
            return _failure(issue_datetime, str(exc))

        values: dict[str, list[float] | list[datetime]] = {
            "issue_datetime": [issue_datetime] * SMOKE_HORIZON,
            "datetime": future_times,
        }
        for member in range(SMOKE_MEMBERS):
            trajectory = [
                discharge[-1]
                + self._artifact.precipitation_factor * rain
                + self._artifact.member_increment * member
                for rain in precipitation
            ]
            if not all(math.isfinite(value) for value in trajectory):
                return _failure(issue_datetime, "smoke prediction overflowed")
            values[str(member + 1)] = trajectory
        frame = pl.DataFrame(values).with_columns(
            pl.col("issue_datetime", "datetime").cast(pl.Datetime("us", "UTC"))
        )
        return fi.ModelSuccess(
            output=fi.ModelOutput(
                model_name="deployment_smoke",
                issue_datetime=issue_datetime,
                variables={
                    station_key: {
                        "discharge": fi.VariableOutput(
                            metadata=fi.VariableMetadata(
                                unit=fi.Unit.M3_PER_S,
                                timedelta=SMOKE_STEP,
                                forecast_horizon=SMOKE_HORIZON,
                                offset=2,
                            ),
                            trajectories=fi.TrajectoryData(
                                num_samples=SMOKE_MEMBERS, data=frame
                            ),
                            flags=frozenset(),
                            status=fi.VariableStatus.SUCCESS,
                        )
                    }
                },
            )
        )

    def serialize_artifact(self, artifact: object) -> bytes:
        if (
            not isinstance(artifact, DeploymentSmokeArtifact)
            or artifact != self._artifact
        ):
            raise ValueError("smoke artifact configuration mismatch")
        return json.dumps(
            {
                "schema_version": 1,
                "config_hash": artifact.config_hash,
                "precipitation_factor": artifact.precipitation_factor,
                "member_increment": artifact.member_increment,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def deserialize_artifact(self, raw: bytes) -> DeploymentSmokeArtifact:
        parsed = _ArtifactPayload.model_validate_json(raw)
        artifact = DeploymentSmokeArtifact(
            config_hash=parsed.config_hash,
            precipitation_factor=parsed.precipitation_factor,
            member_increment=parsed.member_increment,
        )
        if artifact != self._artifact:
            raise ValueError("smoke artifact configuration mismatch")
        return artifact


def _series_values(
    series: fi.InputSeries,
    expected_times: list[datetime],
    expected_unit: fi.Unit,
    parameter: Literal["discharge", "precipitation"],
) -> list[float]:
    frame = series.data
    if series.unit is not expected_unit:
        raise ValueError("smoke input units do not match the declaration")
    if set(frame.columns) != {"datetime", parameter}:
        raise ValueError("smoke input must contain datetime and one control value")
    dtype = frame.schema["datetime"]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
        raise ValueError("smoke input timestamps must be UTC")
    if frame["datetime"].to_list() != expected_times:
        raise ValueError("smoke input must cover the exact three hourly timestamps")
    column = frame[parameter]
    if (
        not column.dtype.is_numeric()
        or column.null_count()
        or not column.is_finite().all()
    ):
        raise ValueError("smoke input values must be finite numbers")
    result = [float(value) for value in column.to_list()]
    if any(value < 0 for value in result):
        raise ValueError("smoke discharge and precipitation must be nonnegative")
    return result


def _failure(
    issue_datetime: datetime,
    message: str,
    cause: fi.FailureCause = fi.FailureCause.INPUT_DATA,
) -> fi.ModelFailure:
    return fi.ModelFailure(
        model_name="deployment_smoke",
        issue_datetime=issue_datetime,
        cause=cause,
        message=message,
    )
