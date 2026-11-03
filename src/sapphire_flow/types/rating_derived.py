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
    from sapphire_flow.types.native_capture import CaptureId, NativeCapture

POLICY = "native-rating-linear-endpoints-v1"
CAPTURE_POLICY = "native-rating-linear-endpoints-v2"


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
    capture_id: CaptureId | None = None
    evidence_version: int = 1

    def __post_init__(self) -> None:
        if (self.discharge is None) != (self.rejection is not None):
            raise ValueError("derived value requires discharge or rejection")
        if self.discharge is not None and (
            not math.isfinite(self.discharge) or self.discharge < 0
        ):
            raise ValueError("derived discharge must be finite and nonnegative")
        data = json.loads(self.content)
        if canonical_content(data) != self.content or data["policy"] not in (
            POLICY,
            CAPTURE_POLICY,
        ):
            raise ValueError("derived content must be canonical policy evidence")
        if data["policy"] == POLICY:
            if self.evidence_version != 1 or self.capture_id is not None:
                raise ValueError("legacy derived evidence cannot claim a capture")
        elif (
            self.evidence_version != 2
            or self.capture_id is None
            or data["feed"].get("kind") != "native_capture"
            or data["feed"].get("capture_id") != str(self.capture_id)
        ):
            raise ValueError("native derived evidence requires its exact capture")
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


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeCaptureFeed:
    capture: NativeCapture

    @property
    def tenant_id(self) -> TenantId:
        return self.capture.source_version.tenant_id

    @property
    def station_id(self) -> StationId:
        return self.capture.source_version.station_id

    @property
    def endpoint(self) -> str:
        return self.capture.source_version.provider.endpoint

    @property
    def api_station_id(self) -> int:
        return self.capture.source_version.provider.api_station_id

    @property
    def content(self) -> dict[str, object]:
        source = self.capture.source_version
        return {
            "kind": "native_capture",
            "capture_id": self.capture.id,
            "capture_sha256": self.capture.evidence_sha256.value,
            "source_version_sha256": source.version_sha256.value,
            "provider": source.provider.content,
            "level_unit": "m",
            "level_reference": source.level_reference.value,
        }
