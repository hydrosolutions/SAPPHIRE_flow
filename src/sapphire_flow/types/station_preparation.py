"""Synthetic, in-memory station preparation evidence; never admission authority."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import StationKind

if TYPE_CHECKING:
    from sapphire_flow.types.ids import TenantId

MAX_BINDINGS = 64
MAX_TEXT_LENGTH = 256


def _text(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        raise ValueError("text must be nonempty and at most 256 characters")


def _require_type(value: object, expected: type[object]) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"expected {expected.__name__}")


class PreparationProvenance(StrEnum):
    SYNTHETIC = "synthetic"


class EvidenceState(StrEnum):
    ABSENT = "absent"
    UNKNOWN = "unknown"
    HIDDEN = "hidden"
    FOREIGN = "foreign"
    CONFLICT = "conflict"


class PreparationHold(StrEnum):
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    EXISTING_STATION = "existing_station"
    IDENTITY_MISMATCH = "identity_mismatch"
    BINDING_MISMATCH = "binding_mismatch"


class PreparationOutcome(StrEnum):
    OFFLINE_PREFLIGHT_ONLY = "OFFLINE_PREFLIGHT_ONLY"
    HELD = "HELD"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationIdentity:
    tenant_id: TenantId
    network: str
    code: str

    def __post_init__(self) -> None:
        _require_type(self.tenant_id, UUID)
        _text(self.network)
        _text(self.code)


@dataclass(frozen=True, kw_only=True, slots=True)
class ProposedStation:
    identity: StationIdentity
    name: str
    kind: StationKind
    location: GeoCoord

    def __post_init__(self) -> None:
        _text(self.name)
        _require_type(self.identity, StationIdentity)
        _require_type(self.kind, StationKind)
        _require_type(self.location, GeoCoord)
        numbers = (self.location.lon, self.location.lat, self.location.altitude_masl)
        if any(value is not None and not math.isfinite(value) for value in numbers):
            raise ValueError("station numeric metadata must be finite")


@dataclass(frozen=True, kw_only=True, slots=True)
class PackageIdentity:
    package_id: str
    fingerprint: str
    source_fingerprint: str
    derived_fingerprint: str

    def __post_init__(self) -> None:
        for value in (
            self.package_id,
            self.fingerprint,
            self.source_fingerprint,
            self.derived_fingerprint,
        ):
            _text(value)


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparationBinding:
    basin_code: str
    gauge_id: str
    gateway_hru_name: str
    polygon_name: str

    def __post_init__(self) -> None:
        for value in (
            self.basin_code,
            self.gauge_id,
            self.gateway_hru_name,
            self.polygon_name,
        ):
            _text(value)

    @property
    def key(self) -> tuple[str, str]:
        return self.gateway_hru_name, self.polygon_name


def _bindings(values: tuple[PreparationBinding, ...]) -> tuple[PreparationBinding, ...]:
    _require_type(values, tuple)
    if len(values) > MAX_BINDINGS:
        raise ValueError("bindings must be a tuple of at most 64 entries")
    for value in values:
        _require_type(value, PreparationBinding)
    if len({value.key for value in values}) != len(values):
        raise ValueError("duplicate or conflicting binding keys")
    return tuple(sorted(values, key=lambda value: value.key))


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparationRequest:
    provenance: PreparationProvenance
    station: ProposedStation
    package: PackageIdentity
    bindings: tuple[PreparationBinding, ...]

    def __post_init__(self) -> None:
        if self.provenance is not PreparationProvenance.SYNTHETIC:
            raise ValueError("synthetic evidence only")
        _require_type(self.station, ProposedStation)
        _require_type(self.package, PackageIdentity)
        object.__setattr__(self, "bindings", _bindings(self.bindings))
        if not self.bindings:
            raise ValueError("at least one expected binding required")


@dataclass(frozen=True, kw_only=True, slots=True)
class OpaqueEvidence:
    state: EvidenceState

    def __post_init__(self) -> None:
        _require_type(self.state, EvidenceState)


@dataclass(frozen=True, kw_only=True, slots=True)
class VisibleStation:
    provenance: PreparationProvenance
    identity: StationIdentity

    def __post_init__(self) -> None:
        if self.provenance is not PreparationProvenance.SYNTHETIC:
            raise ValueError("synthetic evidence only")
        _require_type(self.identity, StationIdentity)


@dataclass(frozen=True, kw_only=True, slots=True)
class VisiblePackage:
    provenance: PreparationProvenance
    identity: StationIdentity
    package: PackageIdentity
    bindings: tuple[PreparationBinding, ...]

    def __post_init__(self) -> None:
        if self.provenance is not PreparationProvenance.SYNTHETIC:
            raise ValueError("synthetic evidence only")
        _require_type(self.identity, StationIdentity)
        _require_type(self.package, PackageIdentity)
        object.__setattr__(self, "bindings", _bindings(self.bindings))


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparationAssessment:
    request: PreparationRequest
    holds: tuple[PreparationHold, ...]

    def __post_init__(self) -> None:
        _require_type(self.request, PreparationRequest)
        _require_type(self.holds, tuple)
        for hold in self.holds:
            _require_type(hold, PreparationHold)
        object.__setattr__(self, "holds", tuple(sorted(set(self.holds))))

    @property
    def outcome(self) -> PreparationOutcome:
        return (
            PreparationOutcome.HELD
            if self.holds
            else PreparationOutcome.OFFLINE_PREFLIGHT_ONLY
        )
