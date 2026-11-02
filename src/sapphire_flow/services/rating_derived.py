from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

from sapphire_flow.services.rating_conversion import (
    RatingConversionError,
    RatingRange,
    convert_provisional_level,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.rating_derived import (
    POLICY,
    NativeFeedCustody,
    RatingDerivedDischarge,
)
from sapphire_flow.types.rating_reference import (
    OwnerAssumedZeroReference,
    assumed_curve_content,
    canonical_content,
    curve_snapshot,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.rating_reference import (
        RatingReferenceEvidence,
    )


def select_eligible_rating(curves: list[RatingCurve], at: UtcDatetime) -> RatingCurve:
    eligible = [curve for curve in curves if curve.valid_from <= at]
    if not eligible:
        raise ValueError("selected delivery has no eligible historical block")
    newest = max(curve.valid_from for curve in eligible)
    matches = [curve for curve in eligible if curve.valid_from == newest]
    if len(matches) != 1:
        raise ValueError("selected delivery has ambiguous tied blocks")
    return matches[0]


def derive_rating_sample(
    observation: Observation,
    curve: RatingCurve,
    *,
    file_id: UUID,
    file_sha256: str,
    delivered_on: str,
    feed: NativeFeedCustody,
    reference: RatingReferenceEvidence,
    at: UtcDatetime,
) -> RatingDerivedDischarge:
    if observation.timestamp > at or observation.timestamp < curve.valid_from:
        raise ValueError("measurement outside eligible conversion time")
    if (
        (feed.tenant_id, feed.station_id, feed.endpoint, feed.api_station_id)
        != (
            reference.tenant_id,
            reference.station_id,
            reference.endpoint,
            reference.api_station_id,
        )
        or observation.station_id != curve.station_id
        or observation.station_id != feed.station_id
    ):
        raise ValueError("same-gauge feed/reference association required")
    snapshot = curve_snapshot(curve)
    if snapshot.content != assumed_curve_content(reference.curve.content):
        raise ValueError("selected curve differs from exact reference binding")
    assumed = isinstance(reference, OwnerAssumedZeroReference)
    reference_at = reference.binding_at if assumed else reference.verified_at
    if reference_at > at:
        raise ValueError("future reference evidence")
    if not feed.start <= observation.timestamp < feed.end:
        raise ValueError("measurement outside documented native feed custody")
    if observation.parameter != "water_level" or observation.source.value != "measured":
        raise ValueError("native conversion requires genuine measured water level")
    warnings: list[str] = []
    if assumed:
        warnings.append("owner_assumed_same_zero_unverified")
    if curve.valid_to is not None and observation.timestamp >= curve.valid_to:
        warnings.append("rating_expired_at_measurement")
    assessed = (
        observation.qc_status is QcStatus.QC_PASSED
        and bool(observation.qc_rule_version)
        and bool(observation.qc_flags)
    )
    if not assessed:
        warnings.append("qc_unassessed")
    rejected = observation.qc_status in (
        QcStatus.QC_FAILED,
        QcStatus.QC_SUSPECT,
        QcStatus.MISSING,
    ) or any(
        flag.status in (QcStatus.QC_FAILED, QcStatus.QC_SUSPECT, QcStatus.MISSING)
        for flag in observation.qc_flags
    )
    value = None
    rejection = "source_qc_rejected" if rejected else None
    endpoints = None
    if rejection is None:
        if observation.value is None:
            rejection = "missing_source_value"
        elif not math.isfinite(observation.value):
            rejection = "nonfinite_source_value"
    if rejection is None and observation.value is not None:
        level = observation.value + (0.0 if assumed else reference.offset_m)
        try:
            result = convert_provisional_level(level, curve)
            value = result.discharge
            if result.range_flag is not RatingRange.IN_RANGE:
                warnings.append("extrapolated_" + result.range_flag.name.lower())
                points = sorted(
                    cast("list[dict[str, float]]", curve.points),  # pyright: ignore[reportUnknownMemberType]
                    key=lambda point: point["water_level"],
                )
                endpoints = (
                    points[:2]
                    if result.range_flag is RatingRange.BELOW
                    else points[-2:]
                )
        except (RatingConversionError, OverflowError) as exc:
            rejection = str(exc)
    identity = dict(
        tenant_id=feed.tenant_id,
        station_id=observation.station_id,
        observation_id=observation.id,
        rating_curve_id=curve.id,
        file_id=file_id,
    )
    source = asdict(observation)
    if observation.value is not None and not math.isfinite(observation.value):
        literal = (
            "NaN"
            if math.isnan(observation.value)
            else "+Inf"
            if observation.value > 0
            else "-Inf"
        )
        source["value"] = {"nonfinite_float": literal}
    for name in ("qc_read_token", "qc_context_segment", "qc_context_requirement"):
        source.pop(name)
    content = canonical_content(
        {
            **identity,
            "policy": POLICY,
            "source": source,
            "curve": json.loads(snapshot.content),
            "file_sha256": file_sha256,
            "delivered_on": delivered_on,
            "selection": "newest_delivery_then_latest_eligible_start",
            "feed": asdict(feed),
            "reference": asdict(reference),
            "discharge": value,
            "rejection": rejection,
            "warnings": warnings,
            "endpoints": endpoints,
            "level_unit": "m",
            "discharge_unit": "m3/s",
        }
    )
    return RatingDerivedDischarge(
        tenant_id=feed.tenant_id,
        station_id=observation.station_id,
        observation_id=observation.id,
        rating_curve_id=curve.id,
        file_id=file_id,
        timestamp=observation.timestamp,
        discharge=value,
        rejection=rejection,
        warnings=tuple(warnings),
        content=content,
    )
