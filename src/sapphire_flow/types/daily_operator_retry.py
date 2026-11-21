from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.daily_forecast import DailyAttemptState
    from sapphire_flow.types.daily_forecast_claim import (
        DailyClaim,
        DailyClaimKey,
        FrozenDailyAssignment,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, PrincipalId


@dataclass(frozen=True, kw_only=True, slots=True)
class OperatorDailyForecast:
    target_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.target_date, date) or isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.target_date, datetime
        ):
            raise ValueError("operator target must be a calendar date")

    @property
    def issue_time(self) -> UtcDatetime:
        return ensure_utc(datetime.combine(self.target_date, time.min, tzinfo=UTC))


@dataclass(frozen=True, kw_only=True, slots=True)
class OperatorRetryRequest:
    request_id: UUID
    parent_id: UUID
    key: DailyClaimKey
    artifact_id: ArtifactId
    artifact_sha256: str
    configuration_sha256: str
    roster_sha256: str
    actor: PrincipalId
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.key.context, OperatorDailyForecast):
            raise ValueError("operator retry requires an explicit operator context")
        if (
            not self.actor.strip()
            or self.actor != self.actor.strip()
            or len(self.actor) > 128
        ):
            raise ValueError(
                "operator actor must be trimmed, nonempty and at most 128 characters"
            )
        if (
            not self.reason.strip()
            or self.reason != self.reason.strip()
            or len(self.reason) > 500
        ):
            raise ValueError(
                "operator reason must be trimmed, nonempty and at most 500 characters"
            )
        for value in (
            self.artifact_sha256,
            self.configuration_sha256,
            self.roster_sha256,
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(
                    "operator expected identities require SHA256 fingerprints"
                )

    def document(self) -> dict[str, str]:
        return {
            "request_id": str(self.request_id),
            "parent_id": str(self.parent_id),
            "tenant_id": str(self.key.tenant_id),
            "deployment": self.key.deployment,
            "group_id": str(self.key.group_id),
            "model_id": self.key.model_id,
            "data_use": self.key.data_use.value,
            "issued_at": self.key.context.issue_time.isoformat(),
            "artifact_id": str(self.artifact_id),
            "artifact_sha256": self.artifact_sha256,
            "configuration_sha256": self.configuration_sha256,
            "roster_sha256": self.roster_sha256,
            "actor": self.actor,
            "reason": self.reason,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class OperatorDailyClaim:
    id: UUID
    owner_id: UUID
    key: DailyClaimKey
    frozen: FrozenDailyAssignment
    parent_id: UUID
    started_at: UtcDatetime
    request: OperatorRetryRequest
    software_version: str
    parent_configuration_sha256: str

    def __post_init__(self) -> None:
        if self.started_at.utcoffset() != timedelta(0):
            raise ValueError("operator start must be aware UTC")
        if self.key != self.request.key or self.parent_id != self.request.parent_id:
            raise ValueError("operator claim must match its request")
        if self.id != self.request.request_id:
            raise ValueError("operator claim identity must equal its request UUID")
        if not self.software_version.strip():
            raise ValueError("operator retry requires software identity")


class OperatorAdmissionState(Enum):
    CREATED = "created"
    EXISTING = "existing"


@dataclass(frozen=True, kw_only=True, slots=True)
class OperatorRetryAdmission:
    claim: OperatorDailyClaim
    state: DailyAttemptState
    admission: OperatorAdmissionState


def roster_sha256(frozen: FrozenDailyAssignment) -> str:
    members = sorted(
        (str(m.station_id), sorted(m.required_parameters)) for m in frozen.members
    )
    return hashlib.sha256(
        json.dumps(members, separators=(",", ":")).encode()
    ).hexdigest()


class OperationalDailyTrigger(Enum):
    SCHEDULED = "scheduled"
    OPERATOR_RETRY = "operator_retry"


def operational_trigger(
    claim: DailyClaim | OperatorDailyClaim,
) -> OperationalDailyTrigger:
    return (
        OperationalDailyTrigger.OPERATOR_RETRY
        if isinstance(claim, OperatorDailyClaim)
        else OperationalDailyTrigger.SCHEDULED
    )
