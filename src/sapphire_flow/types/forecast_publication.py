from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast_preservation import BackupProofStatus
    from sapphire_flow.types.ids import (
        ForecastId,
        PublicationDecisionId,
        StationId,
        TenantId,
        UserId,
    )


class PublicationAction(Enum):
    PUBLISH = "publish"
    WITHDRAW = "withdraw"


class PublicationEventType(Enum):
    PUBLISHED = "published"
    REPLACED = "replaced"
    WITHDRAWN = "withdrawn"


class PreservationAtPublish(Enum):
    VERIFIED = "verified"
    BACKUP_PENDING = "backup_pending"


class WithdrawalReasonCode(Enum):
    INCORRECT_FORECAST = "incorrect_forecast"
    DATA_ERROR = "data_error"
    OTHER = "other"


class TargetSeparation(Enum):
    SEPARATE = "separate"
    SHARED = "shared"


class RetentionReadiness(Enum):
    READY = "ready"
    NOT_READY = "not_ready"


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationKey:
    tenant_id: TenantId
    station_id: StationId
    parameter: str
    issued_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class PublishRequest:
    forecast_id: ForecastId
    expected_forecast_version: int
    expected_selection_version: int | None
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.expected_forecast_version < 1:
            raise ValueError("expected forecast version must be positive")
        if (
            self.expected_selection_version is not None
            and self.expected_selection_version < 0
        ):
            raise ValueError("expected selection version cannot be negative")
        if not 1 <= len(self.idempotency_key) <= 128:
            raise ValueError("idempotency key must be 1-128 characters")


@dataclass(frozen=True, kw_only=True, slots=True)
class WithdrawRequest:
    forecast_id: ForecastId
    expected_selection_version: int
    reason_code: WithdrawalReasonCode
    reason_text: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.expected_selection_version < 1:
            raise ValueError("expected selection version must be positive")
        if not self.reason_text.strip():
            raise ValueError("withdrawal reason text is required")
        if not 1 <= len(self.idempotency_key) <= 128:
            raise ValueError("idempotency key must be 1-128 characters")


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationDecision:
    id: PublicationDecisionId
    key: PublicationKey
    forecast_id: ForecastId
    actor_user_id: UserId
    action: PublicationAction
    selection_version: int
    forecast_version: int
    preservation_at_publish: PreservationAtPublish | None
    replaced_forecast_id: ForecastId | None
    replaced_decision_id: PublicationDecisionId | None
    reason_code: WithdrawalReasonCode | None
    reason_text: str | None
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationSelection:
    key: PublicationKey
    selected_forecast_id: ForecastId | None
    version: int
    linked_warning_publication_id: UUID | None


@dataclass(frozen=True, kw_only=True, slots=True)
class PublicationEvent:
    sequence: int
    event_type: PublicationEventType
    decision: PublicationDecision
    withdrawn: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class ProtectedBackupHealth:
    status: BackupProofStatus
    backup_id: UUID | None
    restored_at: UtcDatetime | None
    checked_at: UtcDatetime
    target_separation: TargetSeparation
    retention: RetentionReadiness
    manifest_sha256: str | None
