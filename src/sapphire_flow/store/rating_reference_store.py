from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime  # noqa: TC003 - Pydantic resolves annotations
from typing import Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves annotations

import sqlalchemy as sa
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import insert

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
    OwnerAssumedZeroReference,
    RatingReferenceEvidence,
    RatingReferenceProof,
    canonical_content,
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
    document = json.loads(content)
    if isinstance(document, dict) and "basis" in document:
        raise ValueError("verified reference requires legacy untagged content")
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


class _AssumedPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: UUID
    tenant_id: UUID
    station_id: UUID
    rating_curve_id: UUID
    curve: _SnapshotPayload
    endpoint: str
    api_station_id: int
    level_unit: Literal["m"]
    curve_unit: Literal["m"]
    basis: Literal["owner_assumed_same_zero"]
    level_unit_evidence_reference: str
    level_unit_evidence_sha256: str
    curve_unit_evidence_reference: str
    curve_unit_evidence_sha256: str
    assumed_by: str
    assumed_at: AwareDatetime
    authority_reference: str
    authority_sha256: str
    binding_by: str
    binding_at: AwareDatetime
    binding_reference: str
    binding_sha256: str


def parse_reference_evidence(content: str) -> RatingReferenceEvidence:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("reference evidence must be an object")
    if "basis" not in data:
        return parse_reference_proof(content)
    p = _AssumedPayload.model_validate_json(content)
    return OwnerAssumedZeroReference(
        id=RatingReferenceProofId(p.id),
        tenant_id=TenantId(p.tenant_id),
        station_id=StationId(p.station_id),
        rating_curve_id=RatingCurveId(p.rating_curve_id),
        curve=CurveSnapshot(content=p.curve.content),
        endpoint=p.endpoint,
        api_station_id=p.api_station_id,
        level_unit=p.level_unit,
        curve_unit=p.curve_unit,
        level_unit_evidence_reference=p.level_unit_evidence_reference,
        level_unit_evidence_sha256=p.level_unit_evidence_sha256,
        curve_unit_evidence_reference=p.curve_unit_evidence_reference,
        curve_unit_evidence_sha256=p.curve_unit_evidence_sha256,
        assumed_by=p.assumed_by,
        assumed_at=ensure_utc(p.assumed_at),
        authority_reference=p.authority_reference,
        authority_sha256=p.authority_sha256,
        binding_by=p.binding_by,
        binding_at=ensure_utc(p.binding_at),
        binding_reference=p.binding_reference,
        binding_sha256=p.binding_sha256,
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

    def fetch_reference_evidence(
        self, proof_id: RatingReferenceProofId
    ) -> RatingReferenceEvidence | None:
        content = self._fetch(rating_reference_proofs, proof_id)
        return parse_reference_evidence(content) if content is not None else None

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
        if table is rating_reference_proofs:
            reference = parse_reference_evidence(content)
            if isinstance(reference, OwnerAssumedZeroReference) and (
                canonical_content(asdict(reference)) != canonical_content(document)
            ):
                raise ValueError("assumed reference content is not canonical")
            if (
                str(reference.rating_curve_id) != str(row["rating_curve_id"])
                or reference.basis.value != row["reference_basis"]
            ):
                raise ValueError("protected reference basis or curve disagreement")
        return content


class PgProtectedReferenceWriter:
    """Owner-only reference append on the caller's transaction; never commits."""

    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def append_reference_evidence(
        self, evidence: RatingReferenceEvidence
    ) -> RatingReferenceProofId:
        if (
            self._conn.scalar(
                sa.text("SELECT current_setting('transaction_isolation')")
            )
            != "read committed"
        ):
            raise ValueError("protected reference writes require READ COMMITTED")
        if not self._conn.scalar(
            sa.text(
                "SELECT session_user = pg_get_userbyid(relowner) FROM pg_class "
                "WHERE oid = 'public.rating_reference_proofs'::regclass"
            )
        ):
            raise ValueError("protected reference admission requires table owner")
        content = canonical_content(asdict(evidence))
        checked = parse_reference_evidence(content)
        if checked != evidence or canonical_content(asdict(checked)) != content:
            raise ValueError("protected reference content disagreement")
        self._conn.execute(
            insert(rating_reference_proofs)
            .values(
                id=evidence.id,
                tenant_id=evidence.tenant_id,
                station_id=evidence.station_id,
                rating_curve_id=evidence.rating_curve_id,
                reference_basis=evidence.basis.value,
                content=content,
                fingerprint=content_digest(content),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        stored = PgRatingReferenceStore(self._conn).fetch_reference_evidence(
            evidence.id
        )
        if (
            stored != evidence
            or stored is None
            or canonical_content(asdict(stored)) != content
        ):
            raise ValueError(
                "protected reference identity already has different content"
            )
        return evidence.id
