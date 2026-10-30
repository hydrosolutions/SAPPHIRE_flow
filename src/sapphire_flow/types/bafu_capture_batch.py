from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, NewType
from uuid import UUID

from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
from sapphire_flow.types.enums import ObservationSource

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_capture import BafuDischargeCapture
    from sapphire_flow.types.bafu_unit_registry import (
        BafuDischargeKey,
        BafuUnitRegistry,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId, TenantId
    from sapphire_flow.types.observation import RawObservation

BafuCaptureBatchId = NewType("BafuCaptureBatchId", UUID)


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported captured batch value: {type(value).__name__}")


def _document(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_value,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuStationCaptureBatch:
    batch_id: BafuCaptureBatchId
    tenant_id: TenantId
    station_id: StationId
    key: BafuDischargeKey
    received_at: UtcDatetime
    registry: BafuUnitRegistry
    observations: tuple[RawObservation, ...]
    captures: tuple[BafuDischargeCapture, ...]
    input_digest: str = field(init=False)

    @property
    def registry_version(self) -> str:
        return self.registry.version

    @property
    def registry_sha256(self) -> str:
        return self.registry.sha256

    def __post_init__(self) -> None:
        if self.received_at.utcoffset() != timedelta(0):
            raise ValueError("batch received time must be aware UTC")
        if not self.captures:
            raise ValueError("captured batch requires source captures")
        if any(capture.key != self.key for capture in self.captures):
            raise ValueError("capture source binding differs from batch")
        if any(capture.received_at != self.received_at for capture in self.captures):
            raise ValueError("capture received time differs from station batch")
        if any(
            capture.result.resolution.rule not in self.registry.rules
            for capture in self.captures
            if isinstance(capture.result, NormalizedDischargeCapture)
        ):
            raise ValueError("normalized capture rule is absent from batch registry")
        if any(
            self.registry.resolve(
                capture.key, capture.result.measurement_time, now=capture.received_at
            )
            != capture.result.resolution
            for capture in self.captures
            if isinstance(capture.result, NormalizedDischargeCapture)
        ):
            raise ValueError(
                "normalized capture resolution differs from batch registry"
            )
        captures = _unique_captures(self.captures)
        observations = _unique_observations(self.observations, self.station_id)
        normalized = {
            capture.result.measurement_time: capture.result
            for capture in captures
            if isinstance(capture.result, NormalizedDischargeCapture)
        }
        discharge = {
            obs.timestamp: obs for obs in observations if obs.parameter == "discharge"
        }
        if set(normalized) != set(discharge) or any(
            discharge[timestamp].value != result.canonical_value.value
            for timestamp, result in normalized.items()
        ):
            raise ValueError(
                "discharge observations must match every normalized capture"
            )
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "captures", captures)
        document = {
            "schema_version": 1,
            "tenant_id": self.tenant_id,
            "station_id": self.station_id,
            "key": asdict(self.key),
            "received_at": self.received_at,
            "registry_version": self.registry_version,
            "registry_sha256": self.registry_sha256,
            "observations": [asdict(obs) for obs in observations],
            "captures": [asdict(capture) for capture in captures],
        }
        object.__setattr__(
            self,
            "input_digest",
            hashlib.sha256(_document(document).encode()).hexdigest(),
        )


def _unique_observations(
    observations: tuple[RawObservation, ...],
    station_id: StationId,
) -> tuple[RawObservation, ...]:
    unique: dict[tuple[UtcDatetime, str], RawObservation] = {}
    for obs in observations:
        if obs.station_id != station_id:
            raise ValueError("observation station binding differs from batch")
        if (
            obs.source is not ObservationSource.MEASURED
            or obs.delivery_id is not None
            or obs.rating_curve_id is not None
            or obs.rating_curve_correction_version is not None
        ):
            raise ValueError("batch requires source-native measured observations")
        if obs.parameter not in ("discharge", "water_level", "water_temperature"):
            raise ValueError("unexpected BAFU observation parameter")
        if obs.timestamp.utcoffset() != timedelta(0) or not math.isfinite(obs.value):
            raise ValueError("observation requires aware UTC time and finite value")
        key = (obs.timestamp, obs.parameter)
        if key in unique and unique[key] != obs:
            raise ValueError("conflicting observations for one persistence key")
        unique[key] = obs
    return tuple(unique[key] for key in sorted(unique))


def _unique_captures(
    captures: tuple[BafuDischargeCapture, ...],
) -> tuple[BafuDischargeCapture, ...]:
    unique: dict[UtcDatetime | None, BafuDischargeCapture] = {}
    for capture in captures:
        key = capture.result.measurement_time
        if key in unique and unique[key] != capture:
            raise ValueError("conflicting captures for one persistence key")
        unique[key] = capture
    return tuple(
        sorted(unique.values(), key=lambda capture: _document(asdict(capture)))
    )


class BafuAdmissionDisposition(Enum):
    ADMITTED = "admitted"
    REJECTED = "rejected"


class BafuAdmissionDecision(Enum):
    ADMITTED = "admitted"
    RETRY = "retry"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuBatchAdmission:
    batch_id: BafuCaptureBatchId
    decision: BafuAdmissionDecision
    sequence: int | None

    def __post_init__(self) -> None:
        if (
            self.sequence is None
            and self.decision is not BafuAdmissionDecision.SUPERSEDED
        ):
            raise ValueError("admitted batch requires a sequence")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("admission sequence must be positive")
