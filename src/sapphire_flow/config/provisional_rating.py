from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID  # noqa: TC003

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from sapphire_flow.store.rating_reference_store import parse_reference_evidence
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import ModelId, StationId, TenantId
from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.rating_derived import NativeFeedCustody
from sapphire_flow.types.rating_evidence import (
    ApprovedLegacyRatingSegment,
    RatingEvidenceMode,
)
from sapphire_flow.types.rating_reference import canonical_content


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeRatingBinding:
    tenant_id: TenantId
    station_id: StationId
    station_code: str
    model_ids: frozenset[ModelId]
    reference: RatingReferenceEvidence
    custody_start: UtcDatetime | None
    custody_end: UtcDatetime | None
    custody_reference: str | None
    reconciliation_days: int
    evidence_mode: RatingEvidenceMode = RatingEvidenceMode.LEGACY_CUSTODY
    legacy_segments: tuple[ApprovedLegacyRatingSegment, ...] = ()


class _LegacyFeedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    station_id: UUID
    endpoint: str
    api_station_id: int = Field(gt=0)
    evidence_reference: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime


class ApprovedLegacySegmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feed_content: str
    feed_sha256: str
    reference_sha256: str
    fingerprints: tuple[str, ...] = Field(min_length=1)

    def to_domain(self) -> ApprovedLegacyRatingSegment:
        parsed = _LegacyFeedConfig.model_validate_json(self.feed_content)
        feed = NativeFeedCustody(
            tenant_id=TenantId(parsed.tenant_id),
            station_id=StationId(parsed.station_id),
            endpoint=parsed.endpoint,
            api_station_id=parsed.api_station_id,
            evidence_reference=parsed.evidence_reference,
            start=ensure_utc(parsed.start),
            end=ensure_utc(parsed.end),
        )
        return ApprovedLegacyRatingSegment(
            feed=feed,
            feed_content=self.feed_content,
            feed_sha256=Sha256Digest(value=self.feed_sha256),
            reference_sha256=Sha256Digest(value=self.reference_sha256),
            fingerprints=frozenset(Sha256Digest(value=f) for f in self.fingerprints),
        )


class ProvisionalRatingConfig(BaseModel):
    """Scoped opt-in with an attributed exact curve binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: UUID
    station_id: UUID
    station_code: str = Field(min_length=1)
    network: Literal["dhm"] = "dhm"
    model_ids: tuple[str, ...] = Field(min_length=1)
    reference_content: str
    reconciliation_days: int = Field(ge=1, le=366)
    custody_start: AwareDatetime | None = None
    custody_end: AwareDatetime | None = None
    custody_reference: str | None = Field(default=None, min_length=1)
    evidence_mode: Literal["legacy_custody", "native_capture"] = "legacy_custody"
    legacy_segments: tuple[ApprovedLegacySegmentConfig, ...] = ()

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> NativeRatingBinding:
        reference = parse_reference_evidence(self.reference_content)
        if (
            reference.tenant_id != self.tenant_id
            or reference.station_id != self.station_id
        ):
            raise ValueError("rating reference scope differs from configured opt-in")
        mode = RatingEvidenceMode(self.evidence_mode)
        if mode is RatingEvidenceMode.LEGACY_CUSTODY:
            if (
                self.custody_start is None
                or self.custody_end is None
                or self.custody_reference is None
                or self.custody_start >= self.custody_end
                or not self.custody_reference.strip()
                or self.legacy_segments
            ):
                raise ValueError(
                    "native rating requires bounded source custody evidence"
                )
        elif any(
            value is not None
            for value in (self.custody_start, self.custody_end, self.custody_reference)
        ):
            raise ValueError("native capture mode cannot extend static custody")
        segments = tuple(segment.to_domain() for segment in self.legacy_segments)
        from dataclasses import asdict

        reference_digest = Sha256Digest.of(canonical_content(asdict(reference)))
        for segment in segments:
            if (
                segment.feed.tenant_id != self.tenant_id
                or segment.feed.station_id != self.station_id
                or segment.feed.endpoint != reference.endpoint
                or segment.feed.api_station_id != reference.api_station_id
                or segment.reference_sha256 != reference_digest
            ):
                raise ValueError("approved legacy segment differs from exact binding")
        if any(not model.strip() for model in self.model_ids):
            raise ValueError("native rating requires model identities")
        return NativeRatingBinding(
            tenant_id=TenantId(self.tenant_id),
            station_id=StationId(self.station_id),
            station_code=self.station_code,
            model_ids=frozenset(ModelId(m) for m in self.model_ids),
            reference=reference,
            custody_start=ensure_utc(self.custody_start)
            if self.custody_start
            else None,
            custody_end=ensure_utc(self.custody_end) if self.custody_end else None,
            custody_reference=self.custody_reference,
            reconciliation_days=self.reconciliation_days,
            evidence_mode=mode,
            legacy_segments=segments,
        )


if TYPE_CHECKING:
    from sapphire_flow.types.rating_reference import RatingReferenceEvidence
