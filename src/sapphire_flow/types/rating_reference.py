from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import (
        MeasurementFeedEvidenceId,
        ObservationId,
        RatingCurveId,
        RatingReferenceProofId,
        StationId,
        TenantId,
    )
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.rating_curve import RatingCurve


def canonical_content(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, datetime):
            if item.utcoffset() is None:
                raise ValueError("snapshot timestamps must be timezone aware")
            return item.astimezone(UTC).isoformat()
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, Enum):
            return item.value
        raise TypeError("unsupported snapshot value")

    return json.dumps(
        value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True, slots=True)
class MeasurementSnapshot:
    """Immutable complete measured-value provenance, excluding mutable QC."""

    content: str

    def __post_init__(self) -> None:
        raw: object = json.loads(self.content)
        if not isinstance(raw, dict):
            raise ValueError("snapshot must contain an object")
        data = cast("dict[str, Any]", raw)
        fields = {
            "id",
            "station_id",
            "timestamp",
            "parameter",
            "value",
            "source",
            "rating_curve_id",
            "rating_curve_correction_version",
            "created_at",
            "delivery_id",
        }
        if set(data) != fields:
            raise ValueError("measurement snapshot requires complete provenance")
        if canonical_content(data) != self.content:
            raise ValueError("measurement snapshot must be canonical")
        for field in ("timestamp", "created_at"):
            if datetime.fromisoformat(data[field]).utcoffset() is None:
                raise ValueError("measurement snapshot requires aware timestamps")
        if data["parameter"] != "water_level" or data["source"] != "measured":
            raise ValueError("measurement must be a measured water level")
        if (
            isinstance(data["value"], bool)
            or not isinstance(data["value"], (int, float))
            or not math.isfinite(data["value"])
        ):
            raise ValueError("measurement value must be finite")
        if (
            data["rating_curve_id"] is not None
            or data["rating_curve_correction_version"] is not None
        ):
            raise ValueError("measurement must not be derived")


@dataclass(frozen=True, kw_only=True, slots=True)
class CurveSnapshot:
    """Copied full curve content; no mutable point list is retained."""

    content: str

    def __post_init__(self) -> None:
        raw: object = json.loads(self.content)
        if not isinstance(raw, dict):
            raise ValueError("snapshot must contain an object")
        data = cast("dict[str, Any]", raw)
        fields = {
            "id",
            "station_id",
            "version",
            "valid_from",
            "valid_to",
            "points",
            "interpolation",
            "uploaded_by",
            "created_at",
            "delivery_id",
            "rating_type_label",
        }
        if set(data) != fields:
            raise ValueError("curve snapshot requires complete provenance")
        if canonical_content(data) != self.content:
            raise ValueError("curve snapshot must be canonical")
        for field in ("valid_from", "valid_to", "created_at"):
            if (
                data[field] is not None
                and datetime.fromisoformat(data[field]).utcoffset() is None
            ):
                raise ValueError("curve snapshot requires aware timestamps")


def measurement_snapshot(observation: Observation) -> MeasurementSnapshot:
    data = asdict(observation)
    for key in ("qc_status", "qc_flags", "qc_rule_version"):
        del data[key]
    return MeasurementSnapshot(content=canonical_content(data))


def curve_snapshot(curve: RatingCurve) -> CurveSnapshot:
    data = asdict(curve)
    data["points"] = sorted(
        data["points"], key=lambda p: (float(p["water_level"]), canonical_content(p))
    )
    return CurveSnapshot(content=canonical_content(data))


def _check_evidence(
    endpoint: str,
    api_station_id: int,
    evidence_reference: str,
    verified_by: str,
    verified_at: datetime,
) -> None:
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or any(c.isspace() for c in endpoint)
    ):
        raise ValueError("feed endpoint must be sanitized HTTPS")
    if type(api_station_id) is not int or api_station_id <= 0:
        raise ValueError("API station identity must be positive")
    if (
        not evidence_reference.strip()
        or not verified_by.strip()
        or verified_at.utcoffset() is None
    ):
        raise ValueError("evidence requires reference, actor and aware time")


@dataclass(frozen=True, kw_only=True, slots=True)
class MeasurementFeedEvidence:
    id: MeasurementFeedEvidenceId
    tenant_id: TenantId
    station_id: StationId
    observation_id: ObservationId
    measurement: MeasurementSnapshot
    endpoint: str
    api_station_id: int
    evidence_reference: str
    verified_by: str
    verified_at: UtcDatetime

    def __post_init__(self) -> None:
        _check_evidence(
            self.endpoint,
            self.api_station_id,
            self.evidence_reference,
            self.verified_by,
            self.verified_at,
        )
        data = json.loads(self.measurement.content)
        if data["id"] != str(self.observation_id) or data["station_id"] != str(
            self.station_id
        ):
            raise ValueError("measurement association identity mismatch")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingReferenceProof:
    id: RatingReferenceProofId
    tenant_id: TenantId
    station_id: StationId
    rating_curve_id: RatingCurveId
    curve: CurveSnapshot
    endpoint: str
    api_station_id: int
    level_unit: Literal["m"]
    curve_unit: Literal["m"]
    level_reference: Literal["gauge_zero", "masl"]
    curve_reference: Literal["gauge_zero", "masl"]
    offset_m: float
    evidence_reference: str
    verified_by: str
    verified_at: UtcDatetime

    def __post_init__(self) -> None:
        _check_evidence(
            self.endpoint,
            self.api_station_id,
            self.evidence_reference,
            self.verified_by,
            self.verified_at,
        )
        if self.level_unit != "m" or self.curve_unit != "m":
            raise ValueError("reference proof requires metres")
        if self.level_reference not in (
            "gauge_zero",
            "masl",
        ) or self.curve_reference not in ("gauge_zero", "masl"):
            raise ValueError("unknown reference compatibility")
        if isinstance(self.offset_m, bool) or not math.isfinite(self.offset_m):
            raise ValueError("reference transform must be finite metres")
        data = json.loads(self.curve.content)
        if data["id"] != str(self.rating_curve_id) or data["station_id"] != str(
            self.station_id
        ):
            raise ValueError("curve proof identity mismatch")
