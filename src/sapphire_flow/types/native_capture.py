from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, NewType
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sapphire_flow.types.rating_reference import MeasurementSnapshot, canonical_content

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ObservationId, StationId, TenantId
    from sapphire_flow.types.observation import Observation, RawObservation

CaptureId = NewType("CaptureId", UUID)
CaptureBatchId = NewType("CaptureBatchId", UUID)


@dataclass(frozen=True, kw_only=True, slots=True)
class Sha256Digest:
    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 64 or any(
            c not in "0123456789abcdef" for c in self.value
        ):
            raise ValueError("digest must contain 64 lowercase hexadecimal characters")

    @classmethod
    def of(cls, content: str) -> Sha256Digest:
        return cls(value=hashlib.sha256(content.encode("utf-8")).hexdigest())


def canonical_provider_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in endpoint)
            or not parsed.hostname.isascii()
        ):
            raise ValueError("invalid endpoint")
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        authority = host if port in (None, 443) else f"{host}:{port}"
        return urlunsplit(("https", authority, parsed.path.rstrip("/") + "/", "", ""))
    except ValueError:
        raise ValueError("provider endpoint must be sanitized HTTPS") from None


@dataclass(frozen=True, kw_only=True, slots=True)
class CanonicalProviderIdentity:
    endpoint: str
    api_station_id: int

    def __post_init__(self) -> None:
        if canonical_provider_endpoint(self.endpoint) != self.endpoint:
            raise ValueError("provider endpoint must already be canonical")
        if type(self.api_station_id) is not int or self.api_station_id <= 0:
            raise ValueError("provider station identity must be positive")

    @classmethod
    def parse(cls, endpoint: str, api_station_id: int) -> CanonicalProviderIdentity:
        return cls(
            endpoint=canonical_provider_endpoint(endpoint),
            api_station_id=api_station_id,
        )

    @property
    def content(self) -> dict[str, object]:
        return {
            "provider": "bipad",
            "endpoint": self.endpoint,
            "api_station_id": self.api_station_id,
        }


class NativeFetchOutcome(Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    NO_REQUEST = "no_request"


class CapturedLevelReference(Enum):
    UNKNOWN = "unknown"
    GAUGE_ZERO = "gauge_zero"
    MASL = "masl"


@dataclass(frozen=True, kw_only=True, slots=True)
class CaptureRequestInterval:
    lower_exclusive: UtcDatetime
    upper_exclusive: UtcDatetime

    def __post_init__(self) -> None:
        start_offset = self.lower_exclusive.utcoffset()
        end_offset = self.upper_exclusive.utcoffset()
        if start_offset is None or end_offset is None:
            raise ValueError("capture request bounds require UTC timestamps")
        if start_offset.total_seconds() or end_offset.total_seconds():
            raise ValueError("capture request bounds require UTC timestamps")
        if self.lower_exclusive >= self.upper_exclusive:
            raise ValueError("capture request lower bound must precede upper bound")


@dataclass(frozen=True, kw_only=True, slots=True)
class DhmRequestEvidence:
    batch_id: CaptureBatchId
    station_id: StationId
    provider: CanonicalProviderIdentity
    requested_intervals: tuple[CaptureRequestInterval, ...]
    request_started_at: UtcDatetime | None
    retrieved_at: UtcDatetime
    outcome: NativeFetchOutcome
    request_count: int | None
    accepted_bytes: int | None

    def __post_init__(self) -> None:
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (self.request_count, self.accepted_bytes)
        ):
            raise ValueError("capture transport counters must be nonnegative integers")
        for moment in (self.request_started_at, self.retrieved_at):
            if moment is not None:
                offset = moment.utcoffset()
                if offset is None or offset.total_seconds():
                    raise ValueError("capture transport timestamps require UTC")
        if (
            self.request_count == 0 or self.outcome is NativeFetchOutcome.NO_REQUEST
        ) and self.accepted_bytes not in (0, None):
            raise ValueError("no-request evidence cannot claim accepted bytes")
        if self.request_count == 0:
            if self.requested_intervals or self.request_started_at is not None:
                raise ValueError("no-request evidence cannot claim request facts")
        elif (self.request_count is not None or self.requested_intervals) and (
            not self.requested_intervals or self.request_started_at is None
        ):
            raise ValueError("requested evidence requires actual request facts")
        if bool(self.requested_intervals) != (self.request_started_at is not None):
            raise ValueError("request intervals and start must agree")
        if self.outcome is NativeFetchOutcome.COMPLETE and not self.requested_intervals:
            raise ValueError("complete fetch requires actual request facts")
        if self.outcome is NativeFetchOutcome.NO_REQUEST and (
            self.requested_intervals
            or self.request_started_at is not None
            or self.request_count not in (0, None)
        ):
            raise ValueError("no-request outcome cannot claim transport")
        if (
            self.request_started_at is not None
            and self.request_started_at > self.retrieved_at
        ):
            raise ValueError("retrieval cannot precede request start")


@dataclass(frozen=True, kw_only=True, slots=True)
class CapturedSourceVersion:
    tenant_id: TenantId
    station_id: StationId
    observation_id: ObservationId
    measurement: MeasurementSnapshot
    provider: CanonicalProviderIdentity
    level_reference: CapturedLevelReference

    def __post_init__(self) -> None:
        data = json.loads(self.measurement.content)
        if data["id"] != str(self.observation_id) or data["station_id"] != str(
            self.station_id
        ):
            raise ValueError("capture measurement identity differs from scope")
        if data["parameter"] != "water_level" or data["source"] != "measured":
            raise ValueError("native capture requires measured water level")
        value = data["value"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("native capture requires a finite measured value")

    @property
    def content(self) -> str:
        return canonical_content(
            {
                "schema_version": 1,
                "tenant_id": self.tenant_id,
                "station_id": self.station_id,
                "measurement": json.loads(self.measurement.content),
                "provider": self.provider.content,
                "level_unit": "m",
                "level_reference": self.level_reference.value,
            }
        )

    @property
    def version_sha256(self) -> Sha256Digest:
        return Sha256Digest.of(self.content)


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeCapture:
    id: CaptureId
    source_version: CapturedSourceVersion

    @property
    def content(self) -> str:
        return canonical_content(
            {
                "schema_version": 1,
                "id": self.id,
                "source_version": json.loads(self.source_version.content),
            }
        )

    @property
    def evidence_sha256(self) -> Sha256Digest:
        return Sha256Digest.of(self.content)


def canonical_capture_rows(
    observations: tuple[RawObservation, ...],
) -> tuple[RawObservation, ...]:
    unique: dict[tuple[str, str, str, str], RawObservation] = {}
    for row in observations:
        key = (
            str(row.station_id),
            row.timestamp.isoformat(),
            row.parameter,
            row.source.value,
        )
        previous = unique.setdefault(key, row)
        if previous != row:
            raise ValueError("conflicting observations at one capture natural key")
        if not math.isfinite(row.value):
            raise ValueError("capture input values must be finite")
    return tuple(unique[key] for key in sorted(unique))


def accepted_capture_input_digest(
    observations: tuple[RawObservation, ...],
) -> Sha256Digest:
    from dataclasses import asdict

    return Sha256Digest.of(
        canonical_content([asdict(row) for row in canonical_capture_rows(observations)])
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class CaptureBatchContext:
    request: DhmRequestEvidence
    tenant_id: TenantId
    flow_run_id: UUID | None
    accepted_input_sha256: Sha256Digest
    level_reference: CapturedLevelReference

    @property
    def content(self) -> str:
        return canonical_content(
            {
                "schema_version": 1,
                "id": self.request.batch_id,
                "tenant_id": self.tenant_id,
                "station_id": self.request.station_id,
                "flow_run_id": self.flow_run_id,
                "provider": self.request.provider.content,
                "requested_intervals": [
                    {
                        "lower_exclusive": p.lower_exclusive,
                        "upper_exclusive": p.upper_exclusive,
                    }
                    for p in self.request.requested_intervals
                ],
                "request_started_at": self.request.request_started_at,
                "retrieved_at": self.request.retrieved_at,
                "outcome": self.request.outcome.value,
                "request_count": self.request.request_count,
                "accepted_bytes": self.request.accepted_bytes,
                "accepted_input_sha256": self.accepted_input_sha256.value,
                "level_unit": "m",
                "level_reference": self.level_reference.value,
            }
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class CaptureCommit:
    batch_id: CaptureBatchId
    written_ids: tuple[ObservationId, ...]
    observed_versions: tuple[NativeCapture, ...]
    inserted_count: int
    updated_count: int
    unchanged_count: int

    def __post_init__(self) -> None:
        if min(self.inserted_count, self.updated_count, self.unchanged_count) < 0:
            raise ValueError("capture commit counts must be nonnegative")
        if self.inserted_count + self.updated_count != len(self.written_ids):
            raise ValueError("capture commit written identities differ from counts")
        if self.inserted_count + self.updated_count + self.unchanged_count != len(
            self.observed_versions
        ):
            raise ValueError("capture commit observed versions differ from counts")


@dataclass(frozen=True, kw_only=True, slots=True)
class CurrentCapturedObservation:
    observation_id: ObservationId
    timestamp: UtcDatetime
    source_version_sha256: Sha256Digest
    capture_id: CaptureId


@dataclass(frozen=True, kw_only=True, slots=True)
class CaptureRecoveryWindow:
    station_id: StationId
    start: UtcDatetime
    end: UtcDatetime
    current_raw: tuple[CurrentCapturedObservation, ...]
    beyond_window_count: int

    def __post_init__(self) -> None:
        from datetime import timedelta

        if not self.start < self.end <= self.start + timedelta(hours=6):
            raise ValueError("capture recovery requires at most a six-hour window")
        if len(self.current_raw) > 1000 or self.beyond_window_count < 0:
            raise ValueError("invalid capture recovery bounds or backlog count")
        if any(not self.start <= row.timestamp < self.end for row in self.current_raw):
            raise ValueError("capture recovery target falls outside the window")
        if len({row.observation_id for row in self.current_raw}) != len(
            self.current_raw
        ):
            raise ValueError("capture recovery contains duplicate targets")


@dataclass(frozen=True, kw_only=True, slots=True)
class CaptureQcWindow:
    observations: tuple[Observation, ...]
    target_ids: frozenset[ObservationId]

    def __post_init__(self) -> None:
        if len(self.observations) > 1000:
            raise ValueError("capture QC context exceeds 1000 rows")
        if not self.target_ids <= {row.id for row in self.observations}:
            raise ValueError("capture QC target is absent from its context")
