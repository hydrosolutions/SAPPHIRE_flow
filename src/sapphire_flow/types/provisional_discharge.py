from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, cast

from sapphire_flow.types.rating_reference import (
    OwnerAssumedZeroReference,
    RatingReferenceProof,
    canonical_content,
    content_digest,
)

if TYPE_CHECKING:
    from sapphire_flow.types.ids import (
        MeasurementFeedEvidenceId,
        ObservationId,
        RatingCurveId,
        RatingReferenceProofId,
        StationId,
        TenantId,
    )
    from sapphire_flow.types.rating_reference import CurveSnapshot, MeasurementSnapshot


CONVERSION_VERSION = "expired-rating-linear-reference-v1"
ASSUMED_CONVERSION_VERSION = "expired-rating-owner-assumed-zero-v1"


@dataclass(frozen=True, kw_only=True, slots=True)
class ProvisionalDischarge:
    tenant_id: TenantId
    station_id: StationId
    observation_id: ObservationId
    rating_curve_id: RatingCurveId
    feed_evidence_id: MeasurementFeedEvidenceId
    reference_proof_id: RatingReferenceProofId
    measurement: MeasurementSnapshot
    curve: CurveSnapshot
    discharge: float
    content: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.discharge):
            raise ValueError("provisional discharge must be finite")
        if content_digest(self.content) != self.fingerprint:
            raise ValueError("provisional fingerprint disagreement")
        document = json.loads(self.content)
        if canonical_content(document) != self.content:
            raise ValueError("provisional content must be canonical")
        if (
            document["measurement"] != json.loads(self.measurement.content)
            or document["curve"] != json.loads(self.curve.content)
            or document["discharge"] != self.discharge
        ):
            raise ValueError("provisional content disagreement")
        reference = document["reference_proof"]
        if not isinstance(reference, dict):
            raise ValueError("provisional reference must be an object")
        reference = cast("dict[str, object]", reference)
        basis = reference.get("basis", "verified")
        versions = {
            "verified": CONVERSION_VERSION,
            "owner_assumed_same_zero": ASSUMED_CONVERSION_VERSION,
        }
        if (
            not isinstance(basis, str)
            or basis not in versions
            or document["conversion_version"] != versions[basis]
        ):
            raise ValueError("provisional reference basis/version disagreement")
        reference_type = (
            OwnerAssumedZeroReference
            if basis == "owner_assumed_same_zero"
            else RatingReferenceProof
        )
        if set(reference) != {field.name for field in fields(reference_type)}:
            raise ValueError("provisional reference fields disagree with basis")
        identities = {
            "id": self.feed_evidence_id,
            "tenant_id": self.tenant_id,
            "station_id": self.station_id,
            "observation_id": self.observation_id,
        }
        if any(document["feed_evidence"][k] != str(v) for k, v in identities.items()):
            raise ValueError("provisional feed identity disagreement")
        identities = {
            "id": self.reference_proof_id,
            "tenant_id": self.tenant_id,
            "station_id": self.station_id,
            "rating_curve_id": self.rating_curve_id,
        }
        if any(document["reference_proof"][k] != str(v) for k, v in identities.items()):
            raise ValueError("provisional reference identity disagreement")
