from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.rating_reference import canonical_content, content_digest

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import (
        ObservationId,
        RatingCurveId,
        StationId,
        TenantId,
    )

POLICY = "native-rating-linear-endpoints-v1"


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingDerivedDischarge:
    tenant_id: TenantId
    station_id: StationId
    observation_id: ObservationId
    rating_curve_id: RatingCurveId
    file_id: UUID
    timestamp: UtcDatetime
    discharge: float | None
    rejection: str | None
    warnings: tuple[str, ...]
    content: str

    def __post_init__(self) -> None:
        if (self.discharge is None) != (self.rejection is not None):
            raise ValueError("derived value requires discharge or rejection")
        if self.discharge is not None and (
            not math.isfinite(self.discharge) or self.discharge < 0
        ):
            raise ValueError("derived discharge must be finite and nonnegative")
        data = json.loads(self.content)
        if canonical_content(data) != self.content or data["policy"] != POLICY:
            raise ValueError("derived content must be canonical policy evidence")
        if data["source"]["timestamp"] != self.timestamp.isoformat():
            raise ValueError("derived timestamp and source evidence disagree")
        if data["discharge"] != self.discharge or data["rejection"] != self.rejection:
            raise ValueError("derived result and evidence disagree")
        if tuple(data["warnings"]) != self.warnings:
            raise ValueError("derived warnings and evidence disagree")
        for field in (
            "tenant_id",
            "station_id",
            "observation_id",
            "rating_curve_id",
            "file_id",
        ):
            if data[field] != str(getattr(self, field)):
                raise ValueError("derived identity and evidence disagree")

    @property
    def fingerprint(self) -> str:
        return content_digest(self.content)


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeFeedCustody:
    tenant_id: TenantId
    station_id: StationId
    endpoint: str
    api_station_id: int
    evidence_reference: str
    start: UtcDatetime
    end: UtcDatetime

    def __post_init__(self) -> None:
        if self.start >= self.end or not self.evidence_reference.strip():
            raise ValueError("native feed custody requires a bounded evidence binding")
        if self.api_station_id <= 0 or not self.endpoint.startswith("https://"):
            raise ValueError("native feed custody requires exact provider identity")
