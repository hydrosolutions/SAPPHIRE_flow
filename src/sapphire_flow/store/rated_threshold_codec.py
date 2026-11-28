# ruff: noqa: TC001 -- Pydantic resolves these boundary annotations.
from __future__ import annotations

import json
from typing import Literal
from uuid import UUID  # noqa: TC003 -- Pydantic boundary annotation

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from sapphire_flow.services.rating_conversion import RatingConverter
from sapphire_flow.store.skill_period_codec import (
    SkillPeriodPayload,
    parse_evaluation_context,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.ids import (
    HindcastForecastId,
    RatingCurveId,
    StationId,
    TenantId,
)
from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.probability import ProbabilityMethod
from sapphire_flow.types.rated_threshold_skill import (
    RatedPeriodSnapshot,
    RatedThresholdCompatibility,
    RatedThresholdDiagnostic,
    RatedThresholdEvaluation,
    RatedThresholdManifest,
    RatedThresholdSkillContext,
    ReportedLevelThreshold,
    ThresholdReferenceBasis,
)
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import CurveSnapshot, canonical_content


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _PointPayload(_Payload):
    water_level: float = Field(strict=True)
    discharge: float = Field(strict=True)


class _CurvePayload(_Payload):
    id: UUID
    station_id: UUID
    version: int = Field(gt=0, strict=True)
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    interpolation: InterpolationMethod
    points: list[_PointPayload] = Field(min_length=2, max_length=4096)
    uploaded_by: UUID | None
    created_at: AwareDatetime
    delivery_id: str | None
    rating_type_label: str | None


def curve_from_snapshot(snapshot: CurveSnapshot) -> RatingCurve:
    p = _CurvePayload.model_validate_json(snapshot.content)
    if p.valid_to is not None and p.valid_to <= p.valid_from:
        raise ValueError("rating snapshot has reversed validity")
    curve = RatingCurve(
        id=RatingCurveId(p.id),
        station_id=StationId(p.station_id),
        version=p.version,
        valid_from=ensure_utc(p.valid_from),
        valid_to=ensure_utc(p.valid_to) if p.valid_to is not None else None,
        interpolation=p.interpolation,
        points=[item.model_dump() for item in p.points],
        uploaded_by=p.uploaded_by,
        created_at=ensure_utc(p.created_at),
        delivery_id=p.delivery_id,
        rating_type_label=p.rating_type_label,
    )
    RatingConverter.from_curve(curve)
    return curve


class _SnapshotPayload(_Payload):
    content: str = Field(max_length=2 * 1024 * 1024)


class _DigestPayload(_Payload):
    value: str = Field(pattern=r"^[0-9a-f]{64}$")


class _LevelPayload(_Payload):
    level: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: float = Field(strict=True)


class RatedThresholdManifestPayload(_Payload):
    station_id: UUID
    tenant_id: UUID
    network: str = Field(min_length=1, max_length=128)
    station_code: str = Field(min_length=1, max_length=128)
    api_station_id: int = Field(gt=0, strict=True)
    source_url: str = Field(max_length=2048)
    retrieved_at: AwareDatetime
    raw_response_sha256: _DigestPayload
    reference_basis: ThresholdReferenceBasis
    reference_operator: str = Field(min_length=1, max_length=256)
    reference_evidence: str = Field(min_length=1, max_length=4096)
    reference_approved_at: AwareDatetime
    thresholds: tuple[_LevelPayload, ...] = Field(min_length=1, max_length=16)
    curves: tuple[_SnapshotPayload, ...] = Field(min_length=1, max_length=128)
    units: Literal["m"] = "m"
    threshold_basis: Literal["current_snapshot_historical_effective_dates_unknown"] = (
        "current_snapshot_historical_effective_dates_unknown"
    )

    def to_domain(self) -> RatedThresholdManifest:
        curves = tuple(CurveSnapshot(content=c.content) for c in self.curves)
        for curve in curves:
            curve_from_snapshot(curve)
        return RatedThresholdManifest(
            station_id=StationId(self.station_id),
            tenant_id=TenantId(self.tenant_id),
            network=self.network,
            station_code=self.station_code,
            api_station_id=self.api_station_id,
            source_url=self.source_url,
            retrieved_at=ensure_utc(self.retrieved_at),
            raw_response_sha256=Sha256Digest(value=self.raw_response_sha256.value),
            reference_basis=self.reference_basis,
            reference_operator=self.reference_operator,
            reference_evidence=self.reference_evidence,
            reference_approved_at=ensure_utc(self.reference_approved_at),
            thresholds=tuple(
                ReportedLevelThreshold(level=t.level, value=t.value)
                for t in self.thresholds
            ),
            curves=curves,
        )


def parse_rated_threshold_manifest(raw: str) -> RatedThresholdManifest:
    if len(raw.encode()) > 2 * 1024 * 1024:
        raise ValueError("rated threshold manifest exceeds 2 MiB")
    return RatedThresholdManifestPayload.model_validate_json(raw).to_domain()


def rated_manifest_content(manifest: RatedThresholdManifest) -> dict[str, object]:
    return json.loads(manifest.content)


class RatedThresholdContextPayload(_Payload):
    schema_version: Literal[2]
    danger_level: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    threshold_value: float
    units: Literal["m"]
    source_url: str
    retrieved_at: AwareDatetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_basis: ThresholdReferenceBasis
    temporal_basis: Literal["declared_period", "legacy_step_aggregate"]
    probability_methods: tuple[ProbabilityMethod, ...]
    paired_count: int = Field(ge=0, strict=True)
    qualified_count: int = Field(ge=0, strict=True)
    scored_count: int = Field(ge=0, strict=True)
    qualified_event_count: int = Field(ge=0, strict=True)
    scored_event_count: int = Field(ge=0, strict=True)
    scored_non_event_count: int = Field(ge=0, strict=True)
    exclusions: tuple[tuple[str, int], ...]
    curve_counts: tuple[tuple[str, int], ...]
    discharge_crossing_min: float | None
    discharge_crossing_max: float | None
    comparison: Literal["strict_gt"]
    decision_probability: float = Field(ge=0, le=1)
    statistic: Literal["water_level_equivalent_of_discharge_aggregate"]
    threshold_basis: Literal["current_snapshot_historical_effective_dates_unknown"]
    reference: Literal["same_scored_subset_event_rate"]

    event_support: Literal["low_event_count", "at_least_ten_each"]

    def to_domain(self) -> RatedThresholdSkillContext:
        result = RatedThresholdSkillContext(
            **(
                self.model_dump(exclude={"event_support"})
                | {"retrieved_at": ensure_utc(self.retrieved_at)}
            )
        )
        if result.event_support != self.event_support:
            raise ValueError("rated threshold event-support flag conflicts with counts")
        return result

    @model_validator(mode="after")
    def validate_context(self) -> RatedThresholdContextPayload:
        self.to_domain()
        return self


class RatedCompatibilityPayload(_Payload):
    operator: str = Field(min_length=1, max_length=256)
    verified_at: AwareDatetime
    consumers: tuple[tuple[str, str], ...] = Field(min_length=5, max_length=5)
    schema_version: Literal[2] = 2

    def to_domain(self) -> RatedThresholdCompatibility:
        return RatedThresholdCompatibility(
            operator=self.operator,
            verified_at=ensure_utc(self.verified_at),
            consumers=self.consumers,
        )


def parse_rated_compatibility(raw: str) -> RatedThresholdCompatibility:
    if len(raw.encode()) > 16384:
        raise ValueError("rated threshold compatibility receipt exceeds 16 KiB")
    return RatedCompatibilityPayload.model_validate_json(raw).to_domain()


class RatedDiagnosticPayload(_Payload):
    lead_time_hours: int
    season: str | None
    flow_regime: str | None
    time_step_seconds: int = Field(gt=0)
    phase_offset_seconds: int | None
    context: RatedThresholdContextPayload
    evaluation_context_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class RatedEvaluationPayload(_Payload):
    manifest: RatedThresholdManifestPayload
    compatibility: RatedCompatibilityPayload
    input_sha256: _DigestPayload
    hindcast_count: int = Field(gt=0, strict=True)
    hindcast_ids: tuple[UUID, ...]
    hindcast_run_ids: tuple[UUID, ...]
    issue_start: AwareDatetime
    issue_end: AwareDatetime
    observation_count: int = Field(ge=0, strict=True)
    diagnostics: tuple[RatedDiagnosticPayload, ...] = Field(max_length=10000)
    evaluation_contexts: dict[str, SkillPeriodPayload] = Field(default_factory=dict)

    def to_domain(self) -> RatedThresholdEvaluation:
        periods: list[RatedPeriodSnapshot] = []
        for digest, value in sorted(self.evaluation_contexts.items()):
            content = value.model_dump(mode="json")
            parse_evaluation_context(content)
            snapshot = RatedPeriodSnapshot(content=canonical_content(content))
            if snapshot.digest != digest:
                raise ValueError("rated period catalogue digest mismatch")
            periods.append(snapshot)
        return RatedThresholdEvaluation(
            evaluation_contexts=tuple(periods),
            manifest=self.manifest.to_domain(),
            compatibility=self.compatibility.to_domain(),
            input_sha256=Sha256Digest(value=self.input_sha256.value),
            hindcast_count=self.hindcast_count,
            hindcast_ids=tuple(
                HindcastForecastId(value) for value in self.hindcast_ids
            ),
            hindcast_run_ids=self.hindcast_run_ids,
            issue_start=ensure_utc(self.issue_start),
            issue_end=ensure_utc(self.issue_end),
            observation_count=self.observation_count,
            diagnostics=tuple(
                RatedThresholdDiagnostic(
                    **(
                        item.model_dump(exclude={"context"})
                        | {"context": item.context.to_domain()}
                    )
                )
                for item in self.diagnostics
            ),
        )


def rated_evaluation_content(
    evaluation: RatedThresholdEvaluation | None,
) -> dict[str, object] | None:
    if evaluation is None:
        return None
    raw = json.loads(canonical_content(evaluation, dataclass_values=True))
    raw["evaluation_contexts"] = {
        item.digest: json.loads(item.content) for item in evaluation.evaluation_contexts
    }
    for item, diagnostic in zip(
        raw["diagnostics"], evaluation.diagnostics, strict=True
    ):
        item["context"]["event_support"] = diagnostic.context.event_support
    payload = RatedEvaluationPayload.model_validate(raw)
    payload.to_domain()
    content = payload.model_dump(mode="json")
    if len(canonical_content(content).encode()) > 2 * 1024 * 1024:
        raise ValueError("rated threshold evaluation exceeds 2 MiB")
    return content


def parse_rated_evaluation(raw: object) -> RatedThresholdEvaluation:
    if len(canonical_content(raw).encode()) > 2 * 1024 * 1024:
        raise ValueError("rated threshold evaluation exceeds 2 MiB")
    return RatedEvaluationPayload.model_validate(raw).to_domain()
