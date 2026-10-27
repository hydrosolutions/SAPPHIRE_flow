from __future__ import annotations

from datetime import timedelta
from typing import Literal, Self
from uuid import UUID  # noqa: TC003 -- Pydantic runtime schema.

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sapphire_flow.types.daily_evaluation import DailyEvaluationSettings
from sapphire_flow.types.daily_output import DailyGridAnchor, DailyOutputContract
from sapphire_flow.types.enums import EnsembleRepresentation  # noqa: TC001 -- Pydantic
from sapphire_flow.types.ids import StationGroupId, TenantId


class DailyOutputContractConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parameter: str
    units: str
    representation: EnsembleRepresentation
    time_step_hours: Literal[24] = 24
    first_lead_hours: int = Field(ge=0)
    horizon_steps: int = Field(gt=0)
    series_keys: tuple[float, ...]
    minimum_horizon_steps: int | None = Field(default=None, ge=1)
    grid_anchor: DailyGridAnchor = DailyGridAnchor.ISSUE_TIME

    def to_domain(self) -> DailyOutputContract:
        return DailyOutputContract(
            parameter=self.parameter,
            units=self.units,
            representation=self.representation,
            time_step=timedelta(hours=self.time_step_hours),
            first_lead=timedelta(hours=self.first_lead_hours),
            horizon_steps=self.horizon_steps,
            series_keys=self.series_keys,
            minimum_horizon_steps=self.minimum_horizon_steps,
            grid_anchor=self.grid_anchor,
        )


class DailyEvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    deployment: str
    prefect_deployment_id: UUID
    group_ids: tuple[UUID, ...]
    model_id: Literal["cmal_small"] = "cmal_small"
    data_use: Literal["standard"] = "standard"
    output_contracts: tuple[DailyOutputContractConfig, ...]

    @model_validator(mode="after")
    def _validate_domain(self) -> Self:
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError("daily group IDs must be unique")
        self.to_domain()
        return self

    def to_domain(self) -> DailyEvaluationSettings:
        return DailyEvaluationSettings(
            tenant_id=TenantId(self.tenant_id),
            deployment=self.deployment,
            prefect_deployment_id=self.prefect_deployment_id,
            group_ids=frozenset(StationGroupId(group) for group in self.group_ids),
            output_contracts=tuple(
                contract.to_domain() for contract in self.output_contracts
            ),
        )
