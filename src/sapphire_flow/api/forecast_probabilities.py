from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from sapphire_flow.api.event_review_schemas import (
    ExceedanceProbabilityResponse,
    ReviewThresholdResponse,
)
from sapphire_flow.services.threshold_probability import (
    threshold_probability,
    unavailable_probability,
)
from sapphire_flow.store.forecast_period_codec import ForecastPeriodPayload
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.probability import ProbabilityMethod
from sapphire_flow.types.time_grid import PeriodLabel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.domain import StationThreshold
    from sapphire_flow.types.forecast import OperationalForecast


class ForecastProbabilityPoint(BaseModel):
    valid_time: datetime
    support_start: datetime | None
    support_end: datetime | None
    threshold_probabilities: dict[str, ExceedanceProbabilityResponse]


class ForecastProbabilitiesResponse(BaseModel):
    kind: Literal["operational"] = "operational"
    availability: Literal[
        "available", "partial", "unavailable", "thresholds_not_configured"
    ]
    reason: str | None
    temporal_support: Literal["declared_period", "unknown"]
    period_definition: ForecastPeriodPayload | None
    thresholds: list[ReviewThresholdResponse]
    points: list[ForecastProbabilityPoint]
    probability_definition: str = (
        "P(stored forecast value > threshold), without tolerance or rounding. "
        "Per valid time; the forecast identity, issue time, QC and provisional "
        "status are those of the parent forecast. Quantile tails have bounds, "
        "not invented point probabilities. Members use empirical frequencies. "
        "Exceedance of an aggregated value is not the probability of a peak "
        "anywhere in its period. Unknown temporal support implies no aggregation."
    )
    threshold_basis: str = (
        "Current stored discharge thresholds in m3/s; updated_at is snapshot "
        "metadata, not a historical effective date."
    )
    window_event_probability: Literal["unsupported"] = "unsupported"


def forecast_probabilities(
    forecast: OperationalForecast, thresholds: Sequence[StationThreshold]
) -> ForecastProbabilitiesResponse:
    ensemble = forecast.ensemble
    period = ensemble.period_definition
    temporal_support: Literal["declared_period", "unknown"] = (
        "declared_period" if period is not None else "unknown"
    )
    period_payload = (
        ForecastPeriodPayload.model_validate(period.content)
        if period is not None
        else None
    )
    if ensemble.parameter != "discharge":
        return ForecastProbabilitiesResponse(
            temporal_support=temporal_support,
            period_definition=period_payload,
            availability="unavailable",
            reason="parameter_not_supported",
            thresholds=[],
            points=[],
        )
    threshold_reason = (
        "threshold_station_mismatch"
        if any(t.station_id != forecast.station_id for t in thresholds)
        else "nonfinite_threshold"
        if any(
            not math.isfinite(t.value) for t in thresholds if t.parameter == "discharge"
        )
        else "duplicate_threshold_keys"
        if len({(t.parameter, t.danger_level, t.source) for t in thresholds})
        != len(thresholds)
        else None
    )
    if threshold_reason is not None:
        return ForecastProbabilitiesResponse(
            temporal_support=temporal_support,
            period_definition=period_payload,
            availability="unavailable",
            reason=threshold_reason,
            thresholds=[],
            points=[],
        )
    snapshot = [
        ReviewThresholdResponse(
            key=json.dumps(
                [t.parameter, t.danger_level, t.source.value], separators=(",", ":")
            ),
            danger_level=t.danger_level,
            value=t.value,
            source=t.source,
            updated_at=t.updated_at,
            parameter="discharge",
            units="m3/s",
        )
        for t in sorted(thresholds, key=lambda t: (t.danger_level, t.source.value))
        if t.parameter == "discharge"
    ]
    if not snapshot:
        return ForecastProbabilitiesResponse(
            temporal_support=temporal_support,
            period_definition=period_payload,
            availability="thresholds_not_configured",
            reason=None,
            thresholds=[],
            points=[],
        )
    reason = (
        "forecast_units_not_canonical_discharge"
        if ensemble.units not in {"m3/s", "m³/s"}
        else f"forecast_{forecast.qc_status.value}"
        if forecast.qc_status
        in {QcStatus.QC_FAILED, QcStatus.QC_SUSPECT, QcStatus.MISSING}
        else None
    )
    label = (
        "quantile"
        if ensemble.representation is EnsembleRepresentation.QUANTILES
        else "member_id"
    )
    points: list[ForecastProbabilityPoint] = []
    for group in ensemble.values.sort("valid_time").partition_by(
        "valid_time", maintain_order=True
    ):
        valid_time = group["valid_time"][0]
        values = list(group.select(label, "value").iter_rows())
        point_reason = reason
        if any(key is None or value is None for key, value in values):
            point_reason = point_reason or "empty_or_nonfinite_values"
        if (
            ensemble.representation is EnsembleRepresentation.MEMBERS
            and len(values) < 2
        ):
            point_reason = point_reason or "deterministic_forecast"
        if ensemble.representation is EnsembleRepresentation.MEMBERS:
            if len({key for key, _ in values}) != len(values):
                point_reason = point_reason or "duplicate_labels"
            values = [(index, value) for index, (_, value) in enumerate(values)]
        probabilities = {
            t.key: ExceedanceProbabilityResponse(
                **asdict(
                    unavailable_probability(point_reason)
                    if point_reason is not None
                    else threshold_probability(ensemble.representation, values, t.value)
                )
            )
            for t in snapshot
        }
        start = None
        end = None
        if period is not None:
            start = (
                valid_time
                if period.label is PeriodLabel.START
                else valid_time - period.grid.step
            )
            end = start + period.grid.step
        points.append(
            ForecastProbabilityPoint(
                valid_time=valid_time,
                support_start=start,
                support_end=end,
                threshold_probabilities=probabilities,
            )
        )
    methods = [
        p.method for point in points for p in point.threshold_probabilities.values()
    ]
    available = sum(method is not ProbabilityMethod.UNAVAILABLE for method in methods)
    return ForecastProbabilitiesResponse(
        temporal_support=temporal_support,
        period_definition=period_payload,
        availability="unavailable"
        if not available
        else "available"
        if available == len(methods)
        else "partial",
        reason=reason or ("no_usable_probabilities" if not available else None),
        thresholds=snapshot,
        points=points,
    )
