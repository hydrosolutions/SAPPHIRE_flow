from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.rating_reference import CurveSnapshot, canonical_content

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import HindcastForecastId, StationId, TenantId
    from sapphire_flow.types.probability import ProbabilityMethod


class ThresholdReferenceBasis(Enum):
    VERIFIED = "verified_common_zero"
    OWNER_ASSUMED = "owner_assumed_same_zero_unverified"


@dataclass(frozen=True, kw_only=True, slots=True)
class ReportedLevelThreshold:
    level: str
    value: float

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.level) is None:
            raise ValueError("reported threshold requires a bounded level name")
        if isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError("reported threshold requires a finite level")


@dataclass(frozen=True, kw_only=True, slots=True)
class RatedThresholdManifest:
    station_id: StationId
    tenant_id: TenantId
    network: str
    station_code: str
    api_station_id: int
    source_url: str
    retrieved_at: UtcDatetime
    raw_response_sha256: Sha256Digest
    reference_basis: ThresholdReferenceBasis
    reference_operator: str
    reference_evidence: str
    reference_approved_at: UtcDatetime
    thresholds: tuple[ReportedLevelThreshold, ...]
    curves: tuple[CurveSnapshot, ...]
    units: Literal["m"] = "m"
    threshold_basis: Literal["current_snapshot_historical_effective_dates_unknown"] = (
        "current_snapshot_historical_effective_dates_unknown"
    )

    def __post_init__(self) -> None:
        from urllib.parse import urlsplit

        url = urlsplit(self.source_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "reported threshold requires a credential-free HTTPS source"
            )
        if type(self.api_station_id) is not int or self.api_station_id < 1:
            raise ValueError(
                "reported threshold requires a positive API station identity"
            )
        if any(
            not value.strip()
            for value in (
                self.network,
                self.station_code,
                self.reference_operator,
                self.reference_evidence,
            )
        ):
            raise ValueError("rated thresholds require station and reference evidence")
        if (
            self.retrieved_at.utcoffset() is None
            or self.reference_approved_at.utcoffset() is None
        ):
            raise ValueError("rated threshold evidence requires aware timestamps")
        if not 1 <= len(self.thresholds) <= 16:
            raise ValueError("rated evaluation requires between 1 and 16 thresholds")
        if len({item.level for item in self.thresholds}) != len(self.thresholds):
            raise ValueError("rated evaluation has duplicate threshold levels")
        named = {item.level: item.value for item in self.thresholds}
        if (
            "warning" in named
            and "danger" in named
            and named["warning"] >= named["danger"]
        ):
            raise ValueError("warning level must be below danger level")
        if not 1 <= len(self.curves) <= 128:
            raise ValueError("rated evaluation requires between 1 and 128 curves")
        curve_ids: set[str] = set()
        for curve in self.curves:
            raw = json.loads(curve.content)
            if raw["id"] in curve_ids:
                raise ValueError("rated evaluation has duplicate curve identities")
            curve_ids.add(raw["id"])
            if raw["station_id"] != str(self.station_id):
                raise ValueError("rating snapshot belongs to another station")
            if not 2 <= len(raw["points"]) <= 4096:
                raise ValueError("rating snapshot requires between 2 and 4096 points")
        if len(self.content.encode()) > 2 * 1024 * 1024:
            raise ValueError("rated threshold manifest exceeds 2 MiB")

    @property
    def content(self) -> str:
        return canonical_content(self, dataclass_values=True)

    @property
    def digest(self) -> Sha256Digest:
        return Sha256Digest.of(self.content)


@dataclass(frozen=True, kw_only=True, slots=True)
class RatedThresholdSkillContext:
    danger_level: str
    threshold_value: float
    source_url: str
    retrieved_at: UtcDatetime
    manifest_sha256: str
    reference_basis: ThresholdReferenceBasis
    temporal_basis: Literal["declared_period", "legacy_step_aggregate"]
    probability_methods: tuple[ProbabilityMethod, ...]
    paired_count: int
    qualified_count: int
    scored_count: int
    qualified_event_count: int
    scored_event_count: int
    scored_non_event_count: int
    exclusions: tuple[tuple[str, int], ...]
    curve_counts: tuple[tuple[str, int], ...]
    discharge_crossing_min: float | None
    discharge_crossing_max: float | None
    schema_version: Literal[2] = 2
    units: Literal["m"] = "m"
    comparison: Literal["strict_gt"] = "strict_gt"
    decision_probability: float = 0.5
    statistic: Literal["water_level_equivalent_of_discharge_aggregate"] = (
        "water_level_equivalent_of_discharge_aggregate"
    )
    threshold_basis: Literal["current_snapshot_historical_effective_dates_unknown"] = (
        "current_snapshot_historical_effective_dates_unknown"
    )
    reference: Literal["same_scored_subset_event_rate"] = (
        "same_scored_subset_event_rate"
    )

    def __post_init__(self) -> None:
        Sha256Digest(value=self.manifest_sha256)
        if (
            not math.isfinite(self.threshold_value)
            or self.retrieved_at.utcoffset() is None
        ):
            raise ValueError(
                "rated threshold requires a finite value and aware retrieval time"
            )
        if self.decision_probability != 0.5:
            raise ValueError("rated threshold decision probability must be 0.5")
        counts = (
            self.paired_count,
            self.qualified_count,
            self.scored_count,
            self.qualified_event_count,
            self.scored_event_count,
            self.scored_non_event_count,
            *(n for _, n in self.exclusions),
            *(n for _, n in self.curve_counts),
        )
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("rated threshold counts must be nonnegative integers")
        if not (
            self.scored_count <= self.qualified_count <= self.paired_count
            and self.scored_event_count
            <= self.qualified_event_count
            <= self.qualified_count
            and self.scored_event_count + self.scored_non_event_count
            == self.scored_count
            and sum(n for _, n in self.exclusions) + self.scored_count
            == self.paired_count
            and sum(n for _, n in self.curve_counts) == self.qualified_count
        ):
            raise ValueError("rated threshold counts are inconsistent")
        bounds = (self.discharge_crossing_min, self.discharge_crossing_max)
        if self.qualified_count:
            if any(value is None or not math.isfinite(value) for value in bounds):
                raise ValueError("qualified rated thresholds require finite crossings")
            assert bounds[0] is not None and bounds[1] is not None
            if bounds[0] > bounds[1]:
                raise ValueError("rated threshold crossing bounds are reversed")
        elif bounds != (None, None):
            raise ValueError("unqualified rated thresholds have no crossing bounds")

    @property
    def excluded_count(self) -> int:
        return self.paired_count - self.scored_count

    @property
    def event_support(self) -> Literal["low_event_count", "at_least_ten_each"]:
        return (
            "low_event_count"
            if min(self.scored_event_count, self.scored_non_event_count) < 10
            else "at_least_ten_each"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RatedThresholdCompatibility:
    operator: str
    verified_at: UtcDatetime
    consumers: tuple[tuple[str, str], ...]
    schema_version: Literal[2] = 2

    def __post_init__(self) -> None:
        required = {"api", "forecast", "scoring", "review", "rollback"}
        if {role for role, _ in self.consumers} != required or len(
            self.consumers
        ) != len(required):
            raise ValueError(
                "rated threshold compatibility requires all five consumer roles"
            )
        if not self.operator.strip() or any(
            not image.strip() for _, image in self.consumers
        ):
            raise ValueError(
                "rated threshold compatibility requires operator and image identities"
            )
        if self.verified_at.utcoffset() is None or self.schema_version != 2:
            raise ValueError(
                "rated threshold compatibility requires an aware schema-2 receipt"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class RatedThresholdDiagnostic:
    lead_time_hours: int
    season: str | None
    flow_regime: str | None
    time_step_seconds: int
    phase_offset_seconds: int | None
    context: RatedThresholdSkillContext
    evaluation_context_sha256: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class RatedPeriodSnapshot:
    content: str

    def __post_init__(self) -> None:
        if canonical_content(json.loads(self.content)) != self.content:
            raise ValueError("rated period snapshot must be canonical")

    @property
    def digest(self) -> str:
        return Sha256Digest.of(self.content).value


@dataclass(frozen=True, kw_only=True, slots=True)
class RatedThresholdEvaluation:
    manifest: RatedThresholdManifest
    compatibility: RatedThresholdCompatibility | None
    input_sha256: Sha256Digest
    hindcast_count: int
    hindcast_ids: tuple[HindcastForecastId, ...]
    hindcast_run_ids: tuple[UUID, ...]
    issue_start: UtcDatetime
    issue_end: UtcDatetime
    observation_count: int
    diagnostics: tuple[RatedThresholdDiagnostic, ...]
    evaluation_contexts: tuple[RatedPeriodSnapshot, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evaluation_contexts",
            tuple(sorted(self.evaluation_contexts, key=lambda item: item.digest)),
        )
        object.__setattr__(
            self,
            "diagnostics",
            tuple(
                sorted(
                    self.diagnostics,
                    key=lambda item: canonical_content(item, dataclass_values=True),
                )
            ),
        )
        if self.hindcast_count < 1 or self.observation_count < 0:
            raise ValueError("rated evaluation requires a nonempty input inventory")
        if (
            len(self.hindcast_ids) != self.hindcast_count
            or len(set(self.hindcast_ids)) != self.hindcast_count
            or not self.hindcast_run_ids
        ):
            raise ValueError(
                "rated evaluation requires complete unique hindcast identities"
            )
        if (
            self.issue_start.utcoffset() is None
            or self.issue_end.utcoffset() is None
            or self.issue_start > self.issue_end
        ):
            raise ValueError("rated evaluation requires ordered aware issue bounds")
        if any(
            item.context.manifest_sha256 != self.manifest.digest.value
            for item in self.diagnostics
        ):
            raise ValueError("rated evaluation diagnostic manifest differs")
        contexts = {item.digest for item in self.evaluation_contexts}
        if len(contexts) != len(self.evaluation_contexts):
            raise ValueError("rated evaluation has duplicate period identities")
        if contexts != {
            item.evaluation_context_sha256
            for item in self.diagnostics
            if item.evaluation_context_sha256 is not None
        }:
            raise ValueError("rated evaluation diagnostic period catalogue differs")
        if any(
            (item.evaluation_context_sha256 is None)
            != (item.context.temporal_basis == "legacy_step_aggregate")
            for item in self.diagnostics
        ):
            raise ValueError("rated diagnostic must identify its declared period")
        if (
            len(canonical_content(self, dataclass_values=True).encode())
            > 2 * 1024 * 1024
        ):
            raise ValueError("rated threshold evaluation exceeds 2 MiB")
