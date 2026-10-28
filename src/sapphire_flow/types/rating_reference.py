from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
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


def _unique_snapshot_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("assumed curve snapshot has duplicate fields")
        result[key] = value
    return result


def _snapshot_number(token: str) -> int | float:
    floating = any(c in token for c in ".eE")
    if not floating and len(token.lstrip("-")) > 4300:
        raise ValueError("assumed curve snapshot integer exceeds 4300 digits")
    value = float(token) if floating else int(token)
    if floating and (
        not math.isfinite(value)
        or (
            value == 0
            and any(c in "123456789" for c in token.lower().partition("e")[0])
        )
    ):
        raise ValueError("assumed curve snapshot has unsafe floating number")
    if token.startswith("-") and value == 0:
        raise ValueError("assumed curve snapshot requires unsigned-zero numbers")
    return value


def _reject_snapshot_constant(token: str) -> None:
    raise ValueError(f"assumed curve snapshot has invalid constant {token}")


def assumed_curve_content(content: str) -> str:
    try:
        data = json.loads(
            content,
            object_pairs_hook=_unique_snapshot_object,
            parse_int=_snapshot_number,
            parse_float=_snapshot_number,
            parse_constant=_reject_snapshot_constant,
        )
        canonical = canonical_content(data)
    except (RecursionError, ValueError) as exc:
        raise ValueError(f"invalid assumed curve snapshot: {exc}") from exc
    try:
        CurveSnapshot(content=canonical)
    except (TypeError, OverflowError) as exc:
        raise ValueError("assumed curve snapshot has invalid provenance types") from exc
    for name in ("id", "station_id", "uploaded_by"):
        value = data[name]
        if name == "uploaded_by" and value is None:
            continue
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("assumed curve snapshot requires canonical UUIDs")
    if type(data["version"]) is not int or data["version"] <= 0:
        raise ValueError("assumed curve snapshot requires integer version")
    if data["interpolation"] not in ("linear", "log_linear"):
        raise ValueError("assumed curve snapshot has invalid interpolation")
    for name in ("delivery_id", "rating_type_label"):
        if data[name] is not None and not isinstance(data[name], str):
            raise ValueError("assumed curve snapshot has invalid provenance")
    for name in ("valid_from", "valid_to", "created_at"):
        value = data[name]
        if name == "valid_to" and value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("assumed curve snapshot requires UTC timestamps")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
            raise ValueError("assumed curve snapshot requires UTC timestamps")
    points = data["points"]
    if not isinstance(points, list) or not points:
        raise ValueError("assumed curve snapshot requires points")
    previous = -math.inf
    for raw_point in cast("list[object]", points):
        if not isinstance(raw_point, dict):
            raise ValueError("assumed curve snapshot has invalid point fields")
        point = cast("dict[str, Any]", raw_point)
        if not {"water_level", "discharge"} <= set(point):
            raise ValueError("assumed curve snapshot has invalid point fields")
        pending: list[tuple[object, int]] = [(point, 0)]
        while pending:
            node, depth = pending.pop()
            if isinstance(node, (dict, list)):
                if depth > 128:
                    raise ValueError(
                        "assumed curve snapshot metadata exceeds depth 128"
                    )
                children = (
                    cast("dict[str, object]", node).values()
                    if isinstance(node, dict)
                    else cast("list[object]", node)
                )
                pending.extend((child, depth + 1) for child in children)
        for name in ("water_level", "discharge"):
            value = point[name]
            if type(value) not in (int, float):
                raise ValueError("assumed curve snapshot requires numeric points")
            try:
                finite = math.isfinite(float(value))
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("assumed curve snapshot requires finite core numbers")
        stage = float(point["water_level"])
        if stage <= previous:
            raise ValueError("assumed curve snapshot requires strictly sorted stages")
        previous = stage
    return canonical


@dataclass(frozen=True, kw_only=True, slots=True)
class AssumedCurveSnapshot:
    content: str

    def __post_init__(self) -> None:
        if assumed_curve_content(self.content) == self.content:
            raise ValueError("canonical assumed curve snapshot requires CurveSnapshot")


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


class RatingReferenceBasis(Enum):
    VERIFIED = "verified"
    OWNER_ASSUMED_SAME_ZERO = "owner_assumed_same_zero"


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
        if not isinstance(cast("object", self.curve), CurveSnapshot):
            raise ValueError("verified reference requires canonical CurveSnapshot")
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

    @property
    def basis(self) -> RatingReferenceBasis:
        return RatingReferenceBasis.VERIFIED


@dataclass(frozen=True, kw_only=True, slots=True)
class OwnerAssumedZeroReference:
    id: RatingReferenceProofId
    tenant_id: TenantId
    station_id: StationId
    rating_curve_id: RatingCurveId
    curve: CurveSnapshot | AssumedCurveSnapshot
    endpoint: str
    api_station_id: int
    level_unit: Literal["m"]
    curve_unit: Literal["m"]
    level_unit_evidence_reference: str
    level_unit_evidence_sha256: str
    curve_unit_evidence_reference: str
    curve_unit_evidence_sha256: str
    assumed_by: str
    assumed_at: UtcDatetime
    authority_reference: str
    authority_sha256: str
    binding_by: str
    binding_at: UtcDatetime
    binding_reference: str
    binding_sha256: str
    basis: Literal[RatingReferenceBasis.OWNER_ASSUMED_SAME_ZERO] = dataclass_field(
        default=RatingReferenceBasis.OWNER_ASSUMED_SAME_ZERO, init=False
    )

    def __post_init__(self) -> None:
        assumed_curve_content(self.curve.content)
        _check_evidence(
            self.endpoint,
            self.api_station_id,
            self.authority_reference,
            self.assumed_by,
            self.assumed_at,
        )
        _check_evidence(
            self.endpoint,
            self.api_station_id,
            self.binding_reference,
            self.binding_by,
            self.binding_at,
        )
        if self.binding_at < self.assumed_at:
            raise ValueError("reference binding predates owner authority")
        if self.level_unit != "m" or self.curve_unit != "m":
            raise ValueError(
                "assumed reference requires independently evidenced metres"
            )
        if any(
            not value.strip()
            for value in (
                self.level_unit_evidence_reference,
                self.curve_unit_evidence_reference,
            )
        ):
            raise ValueError("assumed reference requires independent unit evidence")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                self.level_unit_evidence_sha256,
                self.curve_unit_evidence_sha256,
                self.authority_sha256,
                self.binding_sha256,
            )
        ):
            raise ValueError("assumed reference evidence requires SHA256 digests")
        data = json.loads(self.curve.content)
        if data["id"] != str(self.rating_curve_id) or data["station_id"] != str(
            self.station_id
        ):
            raise ValueError("curve assumption identity mismatch")


RatingReferenceEvidence = RatingReferenceProof | OwnerAssumedZeroReference
