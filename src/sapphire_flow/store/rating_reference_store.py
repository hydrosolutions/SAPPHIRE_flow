from __future__ import annotations

import json
from datetime import datetime  # noqa: TC003 - Pydantic resolves annotations
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves annotations

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from sapphire_flow.db.metadata import measurement_feed_evidence, rating_reference_proofs
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import (
    MeasurementFeedEvidenceId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationId,
    TenantId,
)
from sapphire_flow.types.rating_reference import (
    CurveSnapshot,
    MeasurementFeedEvidence,
    MeasurementSnapshot,
    RatingReferenceProof,
    content_digest,
)


class _EvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    station_id: UUID
    endpoint: str
    api_station_id: int
    evidence_reference: str
    verified_by: str
    verified_at: datetime


class _SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str


class _FeedPayload(_EvidencePayload):
    observation_id: UUID
    measurement: _SnapshotPayload


class _ProofPayload(_EvidencePayload):
    rating_curve_id: UUID
    curve: _SnapshotPayload
    level_unit: Literal["m"]
    curve_unit: Literal["m"]
    level_reference: Literal["gauge_zero", "masl"]
    curve_reference: Literal["gauge_zero", "masl"]
    offset_m: float


def parse_feed_evidence(content: str) -> MeasurementFeedEvidence:
    p = _FeedPayload.model_validate_json(content)
    return MeasurementFeedEvidence(
        id=MeasurementFeedEvidenceId(p.id),
        tenant_id=TenantId(p.tenant_id),
        station_id=StationId(p.station_id),
        observation_id=ObservationId(p.observation_id),
        measurement=MeasurementSnapshot(content=p.measurement.content),
        endpoint=p.endpoint,
        api_station_id=p.api_station_id,
        evidence_reference=p.evidence_reference,
        verified_by=p.verified_by,
        verified_at=ensure_utc(p.verified_at),
    )


def parse_reference_proof(content: str) -> RatingReferenceProof:
    p = _ProofPayload.model_validate_json(content)
    return RatingReferenceProof(
        id=RatingReferenceProofId(p.id),
        tenant_id=TenantId(p.tenant_id),
        station_id=StationId(p.station_id),
        rating_curve_id=RatingCurveId(p.rating_curve_id),
        curve=CurveSnapshot(content=p.curve.content),
        endpoint=p.endpoint,
        api_station_id=p.api_station_id,
        level_unit=p.level_unit,
        curve_unit=p.curve_unit,
        level_reference=p.level_reference,
        curve_reference=p.curve_reference,
        offset_m=p.offset_m,
        evidence_reference=p.evidence_reference,
        verified_by=p.verified_by,
        verified_at=ensure_utc(p.verified_at),
    )


class PgRatingReferenceStore:
    """Protected reader only. No approval, feed attestation or activation writer."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_feed_evidence(
        self, evidence_id: MeasurementFeedEvidenceId
    ) -> MeasurementFeedEvidence | None:
        content = self._fetch(measurement_feed_evidence, evidence_id)
        return parse_feed_evidence(content) if content is not None else None

    def fetch_reference_proof(
        self, proof_id: RatingReferenceProofId
    ) -> RatingReferenceProof | None:
        content = self._fetch(rating_reference_proofs, proof_id)
        return parse_reference_proof(content) if content is not None else None

    def _fetch(self, table: sa.Table, identity: UUID) -> str | None:
        row = (
            self._conn.execute(sa.select(table).where(table.c.id == identity))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        content: str = row["content"]
        if content_digest(content) != row["fingerprint"]:
            raise ValueError("protected reference fingerprint disagreement")
        document = json.loads(content)
        for name in ("id", "tenant_id", "station_id"):
            if document[name] != str(row[name]):
                raise ValueError("protected reference identity disagreement")
        return content
