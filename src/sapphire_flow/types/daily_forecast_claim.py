from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.daily_forecast_outcome import DailyMemberState
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.enums import ForecastDataUse

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.daily_forecast import (
        DailyAttemptOrdinal,
        ScheduledDailyForecast,
    )
    from sapphire_flow.types.daily_forecast_outcome import DailyMemberOutcome
    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, TenantId


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyClaimKey:
    tenant_id: TenantId
    deployment: str
    group_id: StationGroupId
    model_id: ModelId
    context: ScheduledDailyForecast
    data_use: ForecastDataUse = ForecastDataUse.STANDARD

    def __post_init__(self) -> None:
        if not self.deployment.strip():
            raise ValueError("daily deployment must not be empty")
        if self.data_use is not ForecastDataUse.STANDARD:
            raise ValueError("daily evaluation supports STANDARD data only")


@dataclass(frozen=True, kw_only=True, slots=True)
class FrozenDailyAssignment:
    artifact_id: ArtifactId | None
    configuration_sha256: str
    members: tuple[DailyMemberOutcome, ...]
    output_contracts: tuple[DailyOutputContract, ...] = ()

    def __post_init__(self) -> None:
        if any(member.provenance != DailyInputProvenance() for member in self.members):
            raise ValueError("frozen roster cannot include execution-time provenance")
        parameters = [contract.parameter for contract in self.output_contracts]
        if len(set(parameters)) != len(parameters):
            raise ValueError("daily output contracts must have unique parameters")
        if re.fullmatch(r"[0-9a-f]{64}", self.configuration_sha256) is None:
            raise ValueError("daily configuration requires a SHA256 fingerprint")
        if len({member.station_id for member in self.members}) != len(self.members):
            raise ValueError("frozen roster has duplicate members")
        if any(member.state is not DailyMemberState.PENDING for member in self.members):
            raise ValueError("frozen roster must contain pending members only")


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyClaim:
    id: UUID
    owner_id: UUID
    key: DailyClaimKey
    ordinal: DailyAttemptOrdinal
    frozen: FrozenDailyAssignment
    parent_id: UUID | None
    started_at: UtcDatetime

    def __post_init__(self) -> None:
        if self.started_at.utcoffset() != timedelta(0):
            raise ValueError("started_at must be aware UTC")


class DailyFailureReason(Enum):
    NO_OPERATIONAL_STATIONS = "no_operational_stations"
    SHARED_INPUT_FAILURE = "shared_input_failure"
    CONFIGURATION_CHANGED = "configuration_changed"
    CONFIGURATION_UNAVAILABLE = "configuration_unavailable"
    MODEL_FAILURE = "model_failure"
    INTEGRITY_FAILURE = "integrity_failure"
    INTERNAL_FAILURE = "internal_failure"
    PERSISTENCE_FAILURE = "persistence_failure"
