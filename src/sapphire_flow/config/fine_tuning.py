from __future__ import annotations

import json
import tomllib
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID  # noqa: TC003 — Pydantic resolves at runtime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.fine_tuning import FineTuningRequest, FineTuningWeather
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId

if TYPE_CHECKING:
    from pathlib import Path


class _Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Deployment(_Boundary):
    name: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    operator: str = Field(min_length=1)


class _Model(_Boundary):
    id: str = Field(min_length=1)
    group_id: UUID
    base_artifact_id: UUID


class _Data(_Boundary):
    weather_source: FineTuningWeather
    time_step_hours: int = Field(strict=True, gt=0)
    fitting_start: AwareDatetime
    fitting_end: AwareDatetime
    evaluation_start: AwareDatetime
    evaluation_end: AwareDatetime


class _Training(_Boundary):
    seed: int = Field(strict=True)
    parameters: dict[str, Any]


class FineTuningConfig(_Boundary):
    schema_version: Literal[1]
    request_id: UUID
    deployment: _Deployment
    model: _Model
    data: _Data
    training: _Training

    def to_domain(self) -> FineTuningRequest:
        return FineTuningRequest(
            request_id=self.request_id,
            deployment_name=self.deployment.name,
            tenant_code=self.deployment.tenant,
            operator=self.deployment.operator,
            model_id=ModelId(self.model.id),
            group_id=StationGroupId(self.model.group_id),
            base_artifact_id=ArtifactId(self.model.base_artifact_id),
            weather_source=self.data.weather_source,
            fitting_start=ensure_utc(self.data.fitting_start),
            fitting_end=ensure_utc(self.data.fitting_end),
            evaluation_start=ensure_utc(self.data.evaluation_start),
            evaluation_end=ensure_utc(self.data.evaluation_end),
            time_step_hours=self.data.time_step_hours,
            seed=self.training.seed,
            training_parameters_json=json.dumps(
                self.training.parameters, sort_keys=True, allow_nan=False
            ),
        )


def parse_fine_tuning(value: object) -> FineTuningRequest:
    return FineTuningConfig.model_validate(value).to_domain()


def load_fine_tuning(path: Path) -> FineTuningRequest:
    with path.open("rb") as stream:
        return parse_fine_tuning(tomllib.load(stream))
