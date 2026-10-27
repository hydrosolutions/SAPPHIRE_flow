from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.ids import ModelId

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.ids import StationGroupId, TenantId


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyEvaluationSettings:
    tenant_id: TenantId
    deployment: str
    prefect_deployment_id: UUID
    group_ids: frozenset[StationGroupId]
    output_contracts: tuple[DailyOutputContract, ...]

    @property
    def model_id(self) -> ModelId:
        return ModelId("cmal_small")

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.deployment) is None:
            raise ValueError("daily deployment must be a bounded nonempty identifier")
        if not self.group_ids:
            raise ValueError("daily evaluation requires explicit groups")
        parameters = [contract.parameter for contract in self.output_contracts]
        if not parameters or len(parameters) != len(set(parameters)):
            raise ValueError(
                "daily output contracts must have nonempty unique parameters"
            )
        if any(
            contract.time_step != timedelta(days=1)
            or contract.first_lead % contract.time_step != timedelta(0)
            for contract in self.output_contracts
        ):
            raise ValueError(
                "daily output contracts require whole-day cadence and leads"
            )
