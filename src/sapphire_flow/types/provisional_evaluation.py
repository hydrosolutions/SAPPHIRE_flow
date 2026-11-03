from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId, TenantId


class EvaluationCapability(Enum):
    PROVISIONAL_READ = "provisional_read"


class EvaluationSubjectKind(Enum):
    SERVICE_TOKEN = "service_token"
    HUMAN_USER = "human_user"


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationScope:
    tenant_id: TenantId
    station_id: StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationSubject:
    kind: EvaluationSubjectKind
    id: UUID


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationGrant:
    id: UUID
    scope: EvaluationScope
    subject: EvaluationSubject
    granted_at: UtcDatetime
    granted_by: str
    revoked_at: UtcDatetime | None = None
    revoked_by: str | None = None

    def __post_init__(self) -> None:
        if not self.granted_by.strip():
            raise ValueError("evaluation grant requires operator attribution")
        if (self.revoked_at is None) != (self.revoked_by is None):
            raise ValueError("evaluation revocation requires time and operator")
        if self.revoked_at is not None and (
            self.revoked_at < self.granted_at or not (self.revoked_by or "").strip()
        ):
            raise ValueError("invalid evaluation grant revocation")
