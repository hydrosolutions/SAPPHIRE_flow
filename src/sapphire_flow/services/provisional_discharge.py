from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import TYPE_CHECKING

from sapphire_flow.services.rating_conversion import (
    RatingConversionError,
    RatingConverter,
    RatingRange,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.provisional_discharge import ProvisionalDischarge
from sapphire_flow.types.rating_reference import (
    canonical_content,
    content_digest,
    curve_snapshot,
    measurement_snapshot,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.rating_reference import (
        MeasurementFeedEvidence,
        RatingReferenceProof,
    )

CONVERSION_VERSION = "expired-rating-linear-reference-v1"


def convert_provisional_discharge(
    *,
    observation: Observation,
    curves: list[RatingCurve],
    feed_evidence: MeasurementFeedEvidence | None,
    reference_proof: RatingReferenceProof | None,
    at: UtcDatetime,
) -> ProvisionalDischarge:
    """Pure validation/conversion. Persistence must recheck locked source rows."""
    if feed_evidence is None or reference_proof is None:
        raise ValueError("persisted feed evidence and reference proof are required")
    if observation.qc_status != QcStatus.QC_PASSED or not observation.qc_rule_version:
        raise ValueError("persisted QC_PASSED and QC rule generation are required")
    if not observation.qc_flags or any(
        f.status != QcStatus.QC_PASSED for f in observation.qc_flags
    ):
        raise ValueError("applicable passing QC rules are required")
    measurement = measurement_snapshot(observation)
    if measurement != feed_evidence.measurement:
        raise ValueError("measurement differs from feed evidence")
    if (
        feed_evidence.tenant_id,
        feed_evidence.station_id,
        feed_evidence.endpoint,
        feed_evidence.api_station_id,
    ) != (
        reference_proof.tenant_id,
        reference_proof.station_id,
        reference_proof.endpoint,
        reference_proof.api_station_id,
    ):
        raise ValueError("feed/reference proof association mismatch")
    if feed_evidence.station_id != observation.station_id:
        raise ValueError("measurement station mismatch")
    if not curves or any(c.station_id != observation.station_id for c in curves):
        raise ValueError("same-gauge curves are required")
    ordered = sorted(curves, key=lambda c: (c.valid_from, c.version), reverse=True)
    curve = ordered[0]
    if len(ordered) > 1 and (curve.valid_from, curve.version) == (
        ordered[1].valid_from,
        ordered[1].version,
    ):
        raise ValueError("ambiguous newest curve")
    if curve.valid_from > at or curve.valid_to is None or curve.valid_to > at:
        raise ValueError("newest curve must already be expired")
    if curve.valid_to <= curve.valid_from:
        raise ValueError("curve validity interval is invalid")
    if feed_evidence.verified_at > at or reference_proof.verified_at > at:
        raise ValueError("reference evidence is future dated")
    if observation.timestamp < curve.valid_from:
        raise ValueError("measurement is before curve validity start")
    if observation.timestamp > at:
        raise ValueError("measurement is future dated")
    snapshot = curve_snapshot(curve)
    if snapshot != reference_proof.curve:
        raise ValueError("curve differs from reference proof")
    assert observation.value is not None  # measurement_snapshot checked this
    level = observation.value + reference_proof.offset_m
    try:
        result = RatingConverter.from_curve(curve).convert(level)
    except (RatingConversionError, OverflowError):
        raise ValueError("rating conversion failed validation") from None
    if result.range_flag != RatingRange.IN_RANGE:
        raise ValueError("level is outside curve range")
    if not math.isfinite(result.discharge):
        raise ValueError("converted discharge must be finite")
    qc = {
        "qc_status": observation.qc_status.value,
        "qc_rule_version": observation.qc_rule_version,
        "qc_flags": sorted(
            (asdict(f) for f in observation.qc_flags), key=canonical_content
        ),
    }
    content = canonical_content(
        {
            "measurement": json.loads(measurement.content),
            "qc": qc,
            "curve": json.loads(snapshot.content),
            "feed_evidence": asdict(feed_evidence),
            "reference_proof": asdict(reference_proof),
            "conversion_version": CONVERSION_VERSION,
            "discharge": result.discharge,
        }
    )
    return ProvisionalDischarge(
        tenant_id=feed_evidence.tenant_id,
        station_id=observation.station_id,
        observation_id=observation.id,
        rating_curve_id=curve.id,
        feed_evidence_id=feed_evidence.id,
        reference_proof_id=reference_proof.id,
        measurement=measurement,
        curve=snapshot,
        discharge=result.discharge,
        content=content,
        fingerprint=content_digest(content),
    )
