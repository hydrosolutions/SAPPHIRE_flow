from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.forecast_rating_history import (
    ForecastRatingHistory,
    RatingHistoryCurve,
    RatingHistoryDay,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sapphire_flow.types.forecast_rating_history import (
        HistoryWarning,
        RatingHistoryEvidence,
        RatingHistorySample,
        RatingHistorySupport,
    )
    from sapphire_flow.types.ids import RatingCurveId


def _curves(
    samples: list[RatingHistorySample], issue: datetime
) -> tuple[RatingHistoryCurve, ...]:
    groups: dict[tuple[RatingCurveId, str], list[RatingHistorySample]] = defaultdict(
        list
    )
    for sample in samples:
        groups[(sample.curve_id, sample.curve_digest)].append(sample)
    result: list[RatingHistoryCurve] = []
    for (curve_id, digest), rows in sorted(groups.items()):
        first = rows[0]
        if any(
            (row.valid_from, row.valid_to) != (first.valid_from, first.valid_to)
            for row in rows
        ):
            raise ValueError("frozen curve identity disagrees")
        warnings: set[HistoryWarning] = {w for row in rows for w in row.warnings}
        if first.valid_to is not None and first.valid_to <= issue:
            warnings.add("rating_expired_at_issue")
        result.append(
            RatingHistoryCurve(
                curve_id=curve_id,
                content_sha256=digest,
                valid_from=first.valid_from,
                valid_to=first.valid_to,
                warnings=tuple(sorted(warnings)),
            )
        )
    return tuple(result)


def _verify_day(
    support: RatingHistorySupport,
    samples: list[RatingHistorySample],
    evidence: RatingHistoryEvidence,
) -> float:
    accepted = [s for s in samples if s.discharge is not None]
    unassessed = sum("qc_unassessed" in s.warnings for s in accepted)
    extrapolated = sum(
        any(w in s.warnings for w in ("extrapolated_above", "extrapolated_below"))
        for s in accepted
    )
    warnings: set[HistoryWarning] = {w for s in samples for w in s.warnings}
    warnings.update(("partial_daily_support", "accepted_measured_discharge_absent"))
    if any(
        s.valid_to is not None and s.valid_to <= evidence.issued_at for s in samples
    ):
        warnings.add("rating_expired_at_issue")
    if any(
        support.day <= t < support.day + timedelta(days=1) for t in evidence.omissions
    ):
        warnings.add("rating_unreconciled_sources_omitted")
    if support.measured_rejected:
        warnings.add("measured_discharge_rejected")
    actual = (
        len(accepted),
        len(samples) - len(accepted),
        extrapolated,
        len(accepted) - unassessed,
        unassessed,
        tuple(sorted(warnings)),
    )
    expected = (
        support.accepted,
        support.rejected,
        support.extrapolated,
        support.assessed,
        support.unassessed,
        tuple(sorted(support.warnings)),
    )
    if actual != expected or not accepted:
        raise ValueError("frozen rating support disagrees")
    if (accepted[0].timestamp, accepted[-1].timestamp) != (support.first, support.last):
        raise ValueError("frozen rating support times disagree")
    value = math.fsum(
        s.discharge / len(accepted) for s in accepted if s.discharge is not None
    )
    if not math.isfinite(value):
        raise ValueError("nonfinite daily rating discharge")
    return value


def build_forecast_rating_history(
    evidence: RatingHistoryEvidence,
) -> ForecastRatingHistory:
    support = {day.day: day for day in evidence.days}
    buckets: dict[datetime, list[RatingHistorySample]] = defaultdict(list)
    for sample in sorted(evidence.samples, key=lambda s: s.timestamp):
        if sample.day not in support:
            raise ValueError("sample has no retained rating day")
        buckets[sample.day].append(sample)
    days: list[RatingHistoryDay] = []
    for offset in range(7):
        day = evidence.start + timedelta(days=offset)
        retained = support.get(day)
        if retained is None:
            days.append(
                RatingHistoryDay(
                    day=day,
                    status="no_retained_derived_day",
                    daily_discharge=None,
                    support=None,
                    curves=(),
                )
            )
            continue
        samples = buckets[day]
        value = _verify_day(retained, samples, evidence)
        days.append(
            RatingHistoryDay(
                day=day,
                status="available",
                daily_discharge=value,
                support=retained,
                curves=_curves(samples, evidence.issued_at),
            )
        )
    return ForecastRatingHistory(
        forecast_id=evidence.forecast_id,
        station_id=evidence.station_id,
        issued_at=evidence.issued_at,
        artifact_id=evidence.artifact_id,
        start=evidence.start,
        end=evidence.end,
        days=tuple(days),
    )
