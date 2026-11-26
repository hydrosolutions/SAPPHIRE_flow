# ruff: noqa: TC001, TC003 -- FastAPI/Pydantic resolve annotations at runtime.
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Protocol

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from sapphire_flow.api.deps import (
    get_deployment_config,
    get_evaluation_clock,
    get_stores,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.provisional_rating import NativeRatingBinding
from sapphire_flow.services.forecast_peaks import (
    ForecastPeak,
    PeakReason,
    remaining_forecast_peaks,
)
from sapphire_flow.services.rating_conversion import (
    InverseRatingStatus,
    RatingConversionError,
    RatingConverter,
)
from sapphire_flow.store.forecast_period_codec import ForecastPeriodPayload
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import RatingCurveId, StationId
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import (
    OwnerAssumedZeroReference,
    RatingReferenceEvidence,
    assumed_curve_content,
    curve_snapshot,
)
from sapphire_flow.types.station import StationConfig

CurveValidity = Literal["valid", "expired", "not_yet_valid"]
PeakCurveValidity = Literal[
    "valid", "expired", "crosses_expiry", "begins_before_validity"
]
ComparisonReason = Literal[
    "reference_not_configured",
    "reference_ambiguous",
    "reference_scope_mismatch",
    "reference_from_future",
    "curve_missing",
    "curve_changed",
    "invalid_curve",
    "unsupported_interpolation",
    "curve_not_yet_valid",
]

ConversionStatus = (
    ComparisonReason
    | PeakReason
    | Literal[
        "available",
        "begins_before_validity",
        "below",
        "above",
        "ambiguous",
        "invalid_conversion",
    ]
)


class ComparisonCurveResponse(BaseModel):
    id: str
    version: int
    valid_from: datetime
    valid_to: datetime | None
    validity: CurveValidity
    interpolation: Literal["linear", "log_linear"]
    curve_level_range: tuple[float, float] | None
    discharge_range: tuple[float, float] | None
    curve_level_unit: Literal["m"] = "m"
    discharge_unit: Literal["m3/s"] = "m3/s"


class PeakWaterLevelResponse(BaseModel):
    quantile: float
    discharge: float | None
    discharge_unit: Literal["m3/s"] = "m3/s"
    valid_time: datetime | None
    support_start: datetime | None
    support_end: datetime | None
    method: Literal["stored_quantile", "member_median", "member_type7"]
    member_count: int | None
    discharge_unavailable_reason: PeakReason | None
    water_level_equivalent: float | None = Field(
        description=(
            "Equivalent level of the discharge statistic; not mean water level "
            "or an instantaneous flood maximum."
        )
    )
    water_level_unit: Literal["m"] = "m"
    water_level_reference: Literal["gauge_zero", "masl"] | None
    conversion_status: ConversionStatus
    range_flag: Literal["in_range", "below", "above", "ambiguous"] | None
    curve_validity: PeakCurveValidity | None
    warnings: list[str]


class PeakWaterLevelComparisonResponse(BaseModel):
    data_use: Literal["provisional_rating"] = "provisional_rating"
    as_of: datetime
    comparison_unavailable_reason: ComparisonReason | None
    curve: ComparisonCurveResponse | None
    reference_basis: Literal["verified", "owner_assumed_same_zero"] | None
    remaining_peaks: list[PeakWaterLevelResponse]
    period_definition: ForecastPeriodPayload | None
    temporal_support: Literal["declared_period", "unknown"]
    statistic_definition: str = (
        "Independent maxima of pointwise P50 and P90 discharge over remaining stored "
        "timesteps, not quantiles of trajectory maxima or event probabilities. P90 "
        "is an upper envelope. Member type-7 quantiles are evaluated in "
        "discharge space."
    )
    window_definition: str = (
        "Declared periods whose end is after as_of, including partly elapsed periods; "
        "otherwise labels at or after as_of with unknown physical support."
    )
    comparison_basis: str = (
        "Read-time equivalent using the exact configured provisional reference; not "
        "the model's output curve at issuance. Match threshold unit and reference "
        "before comparing. An assumed zero is not an explicit datum. Parent QC, "
        "input quality and period aggregation still apply."
    )


class ComparisonCurveStore(Protocol):
    def fetch_curve_by_id(
        self, station_id: StationId, curve_id: RatingCurveId
    ) -> RatingCurve | None: ...


def _curve_validity(curve: RatingCurve, as_of: UtcDatetime) -> CurveValidity:
    if curve.valid_from > as_of:
        return "not_yet_valid"
    return (
        "expired" if curve.valid_to is not None and as_of >= curve.valid_to else "valid"
    )


def _peak_validity(peak: ForecastPeak, curve: RatingCurve) -> PeakCurveValidity | None:
    start = peak.support_start or peak.valid_time
    if start is None:
        return None
    if start < curve.valid_from:
        return "begins_before_validity"
    if curve.valid_to is not None:
        if start >= curve.valid_to:
            return "expired"
        if peak.support_end is not None and peak.support_end > curve.valid_to:
            return "crosses_expiry"
    return "valid"


def _binding_reason(
    binding: NativeRatingBinding,
    station: StationConfig,
    forecast: OperationalForecast,
    curve: RatingCurve | None,
    as_of: UtcDatetime,
) -> ComparisonReason | None:
    reference = binding.reference
    if (
        (binding.tenant_id, binding.station_id, binding.station_code)
        != (station.tenant_id, station.id, station.code)
        or forecast.station_id != station.id
        or forecast.model_id not in binding.model_ids
        or (reference.tenant_id, reference.station_id)
        != (station.tenant_id, station.id)
    ):
        return "reference_scope_mismatch"
    at = (
        reference.binding_at
        if isinstance(reference, OwnerAssumedZeroReference)
        else reference.verified_at
    )
    if at > as_of:
        return "reference_from_future"
    if curve is None:
        return "curve_missing"
    try:
        unchanged = curve_snapshot(curve).content == assumed_curve_content(
            reference.curve.content
        )
    except (TypeError, ValueError, OverflowError):
        return "invalid_curve"
    if curve.id != reference.rating_curve_id or not unchanged:
        return "curve_changed"
    if curve.interpolation is not InterpolationMethod.LINEAR:
        return "unsupported_interpolation"
    if curve.valid_from > as_of:
        return "curve_not_yet_valid"
    return None


def _convert_peak(
    peak: ForecastPeak,
    *,
    curve: RatingCurve | None,
    reference: RatingReferenceEvidence | None,
    converter: RatingConverter | None,
    reason: ComparisonReason | None,
    as_of: UtcDatetime,
) -> PeakWaterLevelResponse:
    validity = _peak_validity(peak, curve) if curve is not None else None
    assumed = isinstance(reference, OwnerAssumedZeroReference)
    frame = reference.level_reference if reference is not None and not assumed else None
    warnings: list[str] = []
    if assumed:
        warnings.append("owner_assumed_same_zero_unverified")
    if curve is not None and _curve_validity(curve, as_of) == "expired":
        warnings.append("rating_expired_at_comparison")
    if validity in {"expired", "crosses_expiry"}:
        warnings.append("rating_" + validity + "_at_peak")
    status: ConversionStatus = (
        reason
        or peak.reason
        or (
            "begins_before_validity"
            if validity == "begins_before_validity"
            else "available"
        )
    )
    level = None
    range_flag = None
    if (
        status == "available"
        and converter is not None
        and reference is not None
        and peak.discharge is not None
    ):
        try:
            result = converter.inverse(peak.discharge)
            range_flag = result.status.value
            status = "available"
            if result.status is not InverseRatingStatus.IN_RANGE:
                status = result.status.value
            if result.water_level is not None:
                level = result.water_level - (0.0 if assumed else reference.offset_m)
                if not math.isfinite(level):
                    level, status = None, "invalid_conversion"
        except (RatingConversionError, OverflowError, ZeroDivisionError):
            status = "invalid_conversion"
    return PeakWaterLevelResponse(
        quantile=peak.quantile.value,
        discharge=peak.discharge,
        valid_time=peak.valid_time,
        support_start=peak.support_start,
        support_end=peak.support_end,
        method=peak.method,
        member_count=peak.member_count,
        discharge_unavailable_reason=peak.reason,
        water_level_equivalent=level,
        water_level_reference=frame,
        conversion_status=status,
        range_flag=range_flag,
        curve_validity=validity,
        warnings=warnings,
    )


class ForecastPeakReader:
    def __init__(
        self,
        *,
        stores: dict[str, Any],
        bindings: Sequence[NativeRatingBinding],
        as_of: UtcDatetime,
    ) -> None:
        self.stores, self.bindings, self.as_of = stores, bindings, as_of

    def read(self, forecast: OperationalForecast) -> PeakWaterLevelComparisonResponse:
        station = self.stores["station_store"].fetch_station(forecast.station_id)
        matches = [
            binding
            for binding in self.bindings
            if (
                binding.station_id == forecast.station_id
                and forecast.model_id in binding.model_ids
                and station is not None
                and binding.tenant_id == station.tenant_id
            )
        ]
        reason: ComparisonReason | None = "reference_not_configured"
        curve: RatingCurve | None = None
        reference: RatingReferenceEvidence | None = None
        converter: RatingConverter | None = None
        if len(matches) > 1:
            reason = "reference_ambiguous"
        elif len(matches) == 1 and station is not None:
            binding = matches[0]
            reference = binding.reference
            store: ComparisonCurveStore = self.stores["rating_curve_store"]
            curve = store.fetch_curve_by_id(
                forecast.station_id, reference.rating_curve_id
            )
            reason = _binding_reason(binding, station, forecast, curve, self.as_of)
            if curve is not None:
                try:
                    converter = RatingConverter.from_curve(curve)
                    if len(converter.stages) < 2 or any(
                        q < 0 for q in converter.discharges
                    ):
                        reason = reason or "invalid_curve"
                except RatingConversionError:
                    reason = reason or "invalid_curve"
        metadata = (
            ComparisonCurveResponse(
                id=str(curve.id),
                version=curve.version,
                valid_from=curve.valid_from,
                valid_to=curve.valid_to,
                validity=_curve_validity(curve, self.as_of),
                interpolation=curve.interpolation.value,
                curve_level_range=(converter.stages[0], converter.stages[-1])
                if converter and converter.stages
                else None,
                discharge_range=(converter.discharges[0], converter.discharges[-1])
                if converter
                else None,
            )
            if curve is not None
            else None
        )
        period = forecast.ensemble.period_definition
        return PeakWaterLevelComparisonResponse(
            as_of=self.as_of,
            comparison_unavailable_reason=reason,
            curve=metadata,
            reference_basis=reference.basis.value if reference else None,
            remaining_peaks=[
                _convert_peak(
                    peak,
                    curve=curve,
                    reference=reference,
                    converter=converter,
                    reason=reason,
                    as_of=self.as_of,
                )
                for peak in remaining_forecast_peaks(forecast, as_of=self.as_of)
            ],
            period_definition=ForecastPeriodPayload.model_validate(period.content)
            if period
            else None,
            temporal_support="declared_period" if period else "unknown",
        )


def get_forecast_peak_reader(
    stores: dict[str, Any] = Depends(get_stores),
    config: DeploymentConfig | None = Depends(get_deployment_config),
    as_of: datetime = Depends(get_evaluation_clock),
    rating_comparison: Literal["provisional_rating"] | None = Query(None),
) -> ForecastPeakReader:
    return ForecastPeakReader(
        stores=stores,
        bindings=tuple(c.to_domain() for c in config.provisional_ratings)
        if config and rating_comparison == "provisional_rating"
        else (),
        as_of=ensure_utc(as_of),
    )
