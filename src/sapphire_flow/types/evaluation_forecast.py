from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import ForecastDataUse

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    from sapphire_flow.types.daily_forecast import DailyAttemptState
    from sapphire_flow.types.daily_forecast_claim import DailyFailureReason
    from sapphire_flow.types.daily_forecast_outcome import (
        DailyMemberOutcome,
        DailyOutputReference,
    )
    from sapphire_flow.types.daily_forecast_recovery import DailyRecoveryStatus
    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ModelId, StationGroupId, TenantId
    from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationReadSettings:
    tenant_id: TenantId
    deployment: str
    group_ids: tuple[StationGroupId, ...]
    daily_model_id: ModelId
    parameters: tuple[str, ...]
    plausibility_models: frozenset[ModelId] = frozenset()

    def __post_init__(self) -> None:
        if (
            not self.deployment.strip()
            or not self.group_ids
            or len(set(self.group_ids)) != len(self.group_ids)
        ):
            raise ValueError("evaluation requires explicit unique group scope")
        if (
            not self.parameters
            or len(set(self.parameters)) != len(self.parameters)
            or any(not p.strip() for p in self.parameters)
        ):
            raise ValueError("evaluation requires unique parameters")
        if self.plausibility_models - {self.daily_model_id}:
            raise ValueError(
                "only the scoped daily model may allow plausibility failures"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationAssignment:
    model_id: ModelId
    priority: int
    contracts: tuple[DailyOutputContract, ...]


class EvaluationSelectionKind(Enum):
    CURRENT = "current"
    FALLBACK = "fallback"
    ABSENT = "absent"


class EvaluationAbsence(Enum):
    NO_USABLE_OUTPUT = "no_usable_output"
    NO_VALID_TIME_COVERAGE = "no_valid_time_coverage"


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationCandidate:
    reference: DailyOutputReference
    record: OperationalForecast | PersistedRejectedForecast
    age: timedelta


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationParameter:
    parameter: str
    selection: EvaluationSelectionKind
    candidate: EvaluationCandidate | None
    absence: EvaluationAbsence | None
    daily_absence: EvaluationAbsence | None = None


class EvaluationDiagnosticAvailability(Enum):
    AVAILABLE = "available"
    POST_AS_OF = "post_as_of"


class EvaluationReferenceUsability(Enum):
    USABLE = "usable"
    SUPERSEDED = "superseded"
    UNAVAILABLE = "unavailable"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationReferenceDiagnostic:
    reference: DailyOutputReference
    usability: EvaluationReferenceUsability


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationDailyDiagnostic:
    group_id: StationGroupId
    issue_time: UtcDatetime
    latest_attempt_id: UUID | None
    latest_state: DailyAttemptState | None
    failure_reason: DailyFailureReason | None
    authoritative_attempt_id: UUID | None
    member: DailyMemberOutcome | None
    recovery_status: DailyRecoveryStatus | None
    availability: EvaluationDiagnosticAvailability = (
        EvaluationDiagnosticAvailability.AVAILABLE
    )
    references: tuple[EvaluationReferenceDiagnostic, ...] = ()


class EvaluationEvidenceAuthority(Enum):
    FINALIZED_SUMMARY = "finalized_summary"
    STORED_ONLY_UNVERIFIED = "stored_only_unverified"
    UNKNOWN = "unknown"


class EvaluationFreshness(Enum):
    CURRENT = "current"
    STALE = "stale"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationModelDiagnostic:
    model_id: ModelId
    freshness: EvaluationFreshness
    latest_result_at: UtcDatetime | None
    age: timedelta | None
    records: tuple[EvaluationCandidate, ...]
    diagnostic_window_start: UtcDatetime | None
    evidence_authority: EvaluationEvidenceAuthority = (
        EvaluationEvidenceAuthority.UNKNOWN
    )
    availability: EvaluationDiagnosticAvailability = (
        EvaluationDiagnosticAvailability.AVAILABLE
    )
    stored_result: DailyOutputReference | None = None
    group_id: StationGroupId | None = None
    issue_time: UtcDatetime | None = None
    latest_attempt_id: UUID | None = None
    latest_state: DailyAttemptState | None = None
    authoritative_attempt_id: UUID | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationForecasts:
    as_of: UtcDatetime
    valid_at: UtcDatetime
    issue_time: UtcDatetime
    parameters: tuple[EvaluationParameter, ...]
    daily: tuple[EvaluationDailyDiagnostic, ...]
    assignments: tuple[EvaluationAssignment, ...]
    models: tuple[EvaluationModelDiagnostic, ...]
    diagnostic_window_start: UtcDatetime
    data_use: ForecastDataUse = ForecastDataUse.STANDARD
