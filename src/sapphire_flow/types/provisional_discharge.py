from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.rating_reference import canonical_content, content_digest

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
