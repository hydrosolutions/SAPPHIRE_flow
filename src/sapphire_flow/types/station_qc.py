from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.config.onboarding import StationQcThresholdSpec
    from sapphire_flow.types.domain import StationQcOverride


class StationQcRejectionKind(StrEnum):
    TENANT_NOT_FOUND = "tenant_not_found"
    TENANT_MISMATCH = "tenant_mismatch"
    STATION_NOT_FOUND = "station_not_found"
    RULE_NOT_FOUND = "rule_not_found"
    PARAMETER_MISMATCH = "parameter_mismatch"
    CADENCE_MISMATCH = "cadence_mismatch"
    MODE_MISMATCH = "mode_mismatch"
    MERGED_INVALID = "merged_invalid"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationQcRejection:
    spec: StationQcThresholdSpec
    reasons: tuple[StationQcRejectionKind, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class StationQcFanOut:
    spec: StationQcThresholdSpec
    rule_versions: tuple[str, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class Resolution:
    overrides: tuple[StationQcOverride, ...]
    rejected: tuple[StationQcRejection, ...]
    not_applicable: tuple[StationQcThresholdSpec, ...]
    fan_out: tuple[StationQcFanOut, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ThresholdConfigOutcome:
    """Plan 402 T1: moved from `flows/ingest_observations.py` so
    `services/station_qc_overrides.py` never imports a flow module."""

    overrides: tuple[StationQcOverride, ...] = ()
    pending: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    not_applicable: tuple[str, ...] = ()
