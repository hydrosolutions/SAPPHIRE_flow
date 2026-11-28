from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from sapphire_flow.services.rating_conversion import (
    InverseRatingStatus,
    RatingConverter,
    RatingRange,
)
from sapphire_flow.services.skill.quantiles import QuantileSample
from sapphire_flow.services.threshold_probability import threshold_probability
from sapphire_flow.store.rated_threshold_codec import curve_from_snapshot
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.rated_threshold_skill import (
    RatedThresholdManifest,
    RatedThresholdSkillContext,
    ReportedLevelThreshold,
)
from sapphire_flow.types.rating_reference import canonical_content, content_digest

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
    from sapphire_flow.types.probability import ProbabilityMethod


@dataclass(frozen=True, kw_only=True, slots=True)
class PairedSkillSample:
    prediction: np.ndarray | QuantileSample
    observed: float
    valid_time: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparedRating:
    content_sha256: str
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None
    converter: RatingConverter
    references: tuple[tuple[str, int], ...] = ()


def prepare_ratings(manifest: RatedThresholdManifest) -> tuple[PreparedRating, ...]:
    unique: dict[str, PreparedRating] = {}
    for snapshot in manifest.curves:
        curve = curve_from_snapshot(snapshot)
        converter = RatingConverter.from_curve(curve)
        digest = content_digest(
            canonical_content(
                {
                    "station_id": curve.station_id,
                    "valid_from": curve.valid_from,
                    "valid_to": curve.valid_to,
                    "interpolation": curve.interpolation,
                    "stages": converter.stages,
                    "discharges": converter.discharges,
                }
            )
        )
        previous = unique.get(digest)
        references = (
            (*previous.references, (str(curve.id), curve.version))
            if previous
            else ((str(curve.id), curve.version),)
        )
        unique[digest] = PreparedRating(
            content_sha256=digest,
            valid_from=curve.valid_from,
            valid_to=curve.valid_to,
            converter=converter,
            references=tuple(sorted(references)),
        )
    return tuple(unique[key] for key in sorted(unique))


def paired_rated_probabilities(
    samples: list[PairedSkillSample],
    threshold: ReportedLevelThreshold,
    manifest: RatedThresholdManifest,
    ratings: tuple[PreparedRating, ...],
    *,
    time_step: timedelta,
    period: ForecastPeriodDefinition | None,
    reference_displacement: timedelta,
) -> tuple[np.ndarray, np.ndarray, RatedThresholdSkillContext]:
    probabilities: list[float] = []
    events: list[bool] = []
    methods: set[ProbabilityMethod] = set()
    exclusions: Counter[str] = Counter()
    uses: Counter[str] = Counter()
    crossings: list[float] = []
    qualified_events = 0
    for sample in samples:
        start, end = (
            period.bounds(sample.valid_time)
            if period is not None
            else (sample.valid_time, ensure_utc(sample.valid_time + time_step))
        )
        support_start = min(start, ensure_utc(start + reference_displacement))
        support_end = max(end, ensure_utc(end + reference_displacement))
        candidates = [
            rating
            for rating in ratings
            if rating.valid_from < support_end
            and (rating.valid_to is None or rating.valid_to > support_start)
        ]
        if len(candidates) != 1:
            exclusions[
                "conflicting_ratings" if candidates else "missing_or_boundary_rating"
            ] += 1
            continue
        rating = candidates[0]
        if rating.valid_from > support_start or (
            rating.valid_to is not None and rating.valid_to < support_end
        ):
            exclusions["missing_or_boundary_rating"] += 1
            continue
        crossing = rating.converter.convert(threshold.value)
        if crossing.range_flag is not RatingRange.IN_RANGE:
            exclusions["threshold_outside_rating_range"] += 1
            continue
        if (
            rating.converter.inverse(crossing.discharge).status
            is not InverseRatingStatus.IN_RANGE
        ):
            exclusions["ambiguous_threshold_plateau"] += 1
            continue
        uses[rating.content_sha256] += 1
        crossings.append(crossing.discharge)
        event = sample.observed > crossing.discharge
        qualified_events += int(event)
        prediction = sample.prediction
        quantiles = isinstance(prediction, QuantileSample)
        values = (
            list(
                zip(
                    prediction.probabilities,
                    prediction.values,
                    strict=True,
                )
            )
            if isinstance(prediction, QuantileSample)
            else [(float(i), float(value)) for i, value in enumerate(prediction)]
        )
        probability = threshold_probability(
            EnsembleRepresentation.QUANTILES
            if quantiles
            else EnsembleRepresentation.MEMBERS,
            values,
            crossing.discharge,
        )
        methods.add(probability.method)
        if probability.probability is None:
            exclusions["unresolved_probability"] += 1
            continue
        probabilities.append(probability.probability)
        events.append(event)
    context = RatedThresholdSkillContext(
        danger_level=threshold.level,
        threshold_value=threshold.value,
        source_url=manifest.source_url,
        retrieved_at=manifest.retrieved_at,
        manifest_sha256=manifest.digest.value,
        reference_basis=manifest.reference_basis,
        temporal_basis="declared_period"
        if period is not None
        else "legacy_step_aggregate",
        probability_methods=tuple(sorted(methods, key=lambda item: item.value)),
        paired_count=len(samples),
        qualified_count=len(crossings),
        scored_count=len(events),
        qualified_event_count=qualified_events,
        scored_event_count=sum(events),
        scored_non_event_count=len(events) - sum(events),
        exclusions=tuple(sorted(exclusions.items())),
        curve_counts=tuple(sorted(uses.items())),
        discharge_crossing_min=min(crossings) if crossings else None,
        discharge_crossing_max=max(crossings) if crossings else None,
    )
    return (
        np.asarray(probabilities, dtype=float),
        np.asarray(events, dtype=bool),
        context,
    )
