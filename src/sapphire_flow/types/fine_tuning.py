from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId


class FineTuningWeather(Enum):
    HYBRID = "hybrid"
    ERA5_LAND = "era5_land"
    RECAP_ERA5_LAND = "recap_era5_land"
    SINGLE = "single"


@dataclass(frozen=True, kw_only=True, slots=True)
class FineTuningRequest:
    request_id: UUID
    deployment_name: str
    tenant_code: str
    operator: str
    model_id: ModelId
    group_id: StationGroupId
    base_artifact_id: ArtifactId
    weather_source: FineTuningWeather
    fitting_start: UtcDatetime
    fitting_end: UtcDatetime
    evaluation_start: UtcDatetime
    evaluation_end: UtcDatetime
    time_step_hours: int
    seed: int
    training_parameters_json: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.deployment_name,
                self.tenant_code,
                self.operator,
                str(self.model_id),
            )
        ):
            raise ValueError("deployment, tenant, operator and model must be explicit")
        dates = (
            self.fitting_start,
            self.fitting_end,
            self.evaluation_start,
            self.evaluation_end,
        )
        if any(date.utcoffset() is None for date in dates):
            raise ValueError("fine-tuning dates must have an explicit timezone")
        if (
            not self.fitting_start
            < self.fitting_end
            <= self.evaluation_start
            < self.evaluation_end
        ):
            raise ValueError("evaluation must follow the nonempty fitting period")
        if self.time_step_hours < 1:
            raise ValueError("time_step_hours must be positive")
        parameters = json.loads(self.training_parameters_json)
        if not isinstance(parameters, dict):
            raise ValueError("training parameters must be an object")
        json.dumps(parameters, allow_nan=False)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "request_id": str(self.request_id),
            "deployment": {
                "name": self.deployment_name,
                "tenant": self.tenant_code,
                "operator": self.operator,
            },
            "model": {
                "id": str(self.model_id),
                "group_id": str(self.group_id),
                "base_artifact_id": str(self.base_artifact_id),
            },
            "data": {
                "weather_source": self.weather_source.value,
                "time_step_hours": self.time_step_hours,
                "fitting_start": self.fitting_start.isoformat(),
                "fitting_end": self.fitting_end.isoformat(),
                "evaluation_start": self.evaluation_start.isoformat(),
                "evaluation_end": self.evaluation_end.isoformat(),
            },
            "training": {
                "seed": self.seed,
                "parameters": json.loads(self.training_parameters_json),
            },
        }

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.payload(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(encoded.encode()).hexdigest()
