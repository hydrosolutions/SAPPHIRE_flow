from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.services.network_overview import reduce_forecast_point
from sapphire_flow.services.threshold_probability import (
    threshold_probability,
    unavailable_probability,
)
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.event_review import (
    MAX_REVIEW_POINTS,
    MAX_REVIEW_SOURCES,
    ReviewAvailability,
    ReviewCatalogLimitError,
    ReviewKind,
    ReviewOptions,
    ReviewPoint,
    ReviewPointState,
    ReviewPolicy,
    ReviewSegment,
    ReviewSeries,
    ReviewSeriesError,
    ReviewSeriesQuery,
    ReviewSeriesRecord,
    ReviewSource,
    ReviewSourceMode,
    ReviewThreshold,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import EventReviewStore
    from sapphire_flow.types.domain import StationThreshold
    from sapphire_flow.types.event_review import ReviewOptionsQuery


def read_review_options(
    store: EventReviewStore, query: ReviewOptionsQuery
) -> ReviewOptions:
    sources = store.fetch_options(query)
    if len(sources) > MAX_REVIEW_SOURCES:
        raise ReviewCatalogLimitError("Review catalog exceeds its source limit")
    if query.policy is ReviewPolicy.PUBLISHED_ONLY and any(
        source.kind is ReviewKind.HINDCAST for source in sources
    ):
        raise ValueError("Protected hindcast metadata returned by review store")
    kinds = {source.kind for source in sources}
    return ReviewOptions(
        station_id=query.station_id,
        sources=sources,
        operational_availability=ReviewAvailability.AVAILABLE
        if ReviewKind.OPERATIONAL in kinds
        else ReviewAvailability.ABSENT,
        hindcast_availability=(
            ReviewAvailability.UNSUPPORTED
            if query.policy is ReviewPolicy.PUBLISHED_ONLY
            else ReviewAvailability.AVAILABLE
            if ReviewKind.HINDCAST in kinds
            else ReviewAvailability.ABSENT
        ),
    )


def _review_point(record: ReviewSeriesRecord) -> ReviewPoint:
    common = ReviewPoint(
        valid_time=record.valid_time,
        issued_at=record.issued_at,
        forecast_id=record.forecast_id,
        stored_at=record.stored_at,
        qc_status=record.qc_status,
        state=ReviewPointState.MISSING,
        reason=None,
    )
    if record.qc_status in {QcStatus.QC_FAILED, QcStatus.QC_SUSPECT}:
        return replace(
            common,
            state=ReviewPointState.QC_REJECTED,
            reason=f"forecast_{record.qc_status.value}",
        )
    if not record.values or record.qc_status is QcStatus.MISSING:
        return replace(
            common, state=ReviewPointState.MISSING, reason="no_usable_values"
        )
    if any(key is None for key, _ in record.values):
        return replace(
            common,
            state=ReviewPointState.UNAVAILABLE,
            reason="values_do_not_match_ensemble_representation",
        )
    try:
        point = reduce_forecast_point(
            record.valid_time,
            [(key, value) for key, value in record.values if key is not None],
            record.representation,
            "discharge",
        )
    except (ValueError, OverflowError):
        return replace(
            common,
            state=ReviewPointState.UNAVAILABLE,
            reason="invalid_values_or_missing_exact_median",
        )
    quantiles = record.representation is EnsembleRepresentation.QUANTILES
    return replace(
        common,
        state=ReviewPointState.AVAILABLE,
        reason="qc_unchecked"
        if record.qc_status in {QcStatus.RAW, QcStatus.QC_UNCHECKED}
        else None,
        median=point.median,
        p25=point.p25,
        p75=point.p75,
        quartile_method="stored_quantiles" if quantiles else "member_empirical_type7",
        band_reason=(
            "exact_quartiles_not_stored" if quantiles else "fewer_than_five_members"
        )
        if point.p25 is None or point.p75 is None
        else None,
    )


def read_review_series(
    store: EventReviewStore,
    query: ReviewSeriesQuery,
    thresholds: tuple[StationThreshold, ...] = (),
) -> ReviewSeries:
    if query.source_mode is not ReviewSourceMode.OPERATIONAL and (
        query.scope.policy is ReviewPolicy.PUBLISHED_ONLY
    ):
        raise ReviewSeriesError(
            "review_source_unsupported",
            "Hindcast review is unavailable under the publication policy.",
        )
    if query.source_mode is ReviewSourceMode.OPERATIONAL_WITH_HINDCAST:
        raise ReviewSeriesError(
            "review_source_unsupported",
            "Temporal support is unknown; select operational or hindcast separately.",
        )
    if any(t.station_id != query.scope.station_id for t in thresholds):
        raise ValueError("Review thresholds belong to another station")
    discharge = [t for t in thresholds if t.parameter == "discharge"]
    if any(not math.isfinite(t.value) for t in discharge):
        raise ValueError("Review thresholds must be finite")
    threshold_snapshot = tuple(
        ReviewThreshold(
            key=json.dumps(
                [t.parameter, t.danger_level, t.source.value], separators=(",", ":")
            ),
            danger_level=t.danger_level,
            value=t.value,
            source=t.source,
            updated_at=t.updated_at,
        )
        for t in sorted(discharge, key=lambda t: (t.danger_level, t.source.value))
    )
    if len({t.key for t in threshold_snapshot}) != len(threshold_snapshot):
        raise ValueError("Review threshold keys must be unique")
    snapshot = store.fetch_series(query)
    for source in snapshot.sources:
        if (
            source.model_id != query.model_id
            or source.kind.value != query.source_mode.value
        ):
            raise ValueError("Review store returned an out-of-selection source")
        if query.source_mode is ReviewSourceMode.HINDCAST and (
            source.hindcast_run_id != query.hindcast_run_id
            or source.artifact_id != query.hindcast_artifact_id
            or source.forcing_type != query.forcing_type
            or source.time_step_seconds != query.time_step_seconds
        ):
            raise ValueError("Review store returned an unpinned hindcast source")
    if not snapshot.sources:
        raise ReviewSeriesError("review_source_not_found", "Review source not found.")
    if not any(query.lead_hours in source.lead_hours for source in snapshot.sources):
        raise ReviewSeriesError(
            "lead_not_offered", "This source does not offer the requested lead."
        )
    if len(snapshot.records) > MAX_REVIEW_POINTS:
        raise ReviewSeriesError(
            "review_series_limit_exceeded", "Too many points; narrow the period."
        )
    timestamps = [record.valid_time for record in snapshot.records]
    if len(timestamps) != len(set(timestamps)):
        raise ReviewSeriesError(
            "review_source_unsupported",
            "Ambiguous forecast revisions; no authoritative selection.",
        )
    grouped: dict[ReviewSource, list[ReviewPoint]] = {}
    for record in snapshot.records:
        if (
            record.source not in snapshot.sources
            or record.source.model_id != query.model_id
            or record.valid_time - record.issued_at != timedelta(hours=query.lead_hours)
            or not query.start <= record.valid_time < query.end
            or (query.source_mode.value != record.source.kind.value)
        ):
            raise ValueError("Review store returned an out-of-selection record")
        point = _review_point(record)
        if point.state is ReviewPointState.AVAILABLE:
            point = replace(
                point,
                threshold_probabilities={
                    t.key: threshold_probability(
                        record.representation,
                        [
                            (key, value)
                            for key, value in record.values
                            if key is not None
                        ],
                        t.value,
                    )
                    if record.source.units in {"m3/s", "m³/s"}
                    else unavailable_probability(
                        "forecast_units_not_canonical_discharge"
                    )
                    for t in threshold_snapshot
                },
            )
        grouped.setdefault(record.source, []).append(point)
    segments: list[ReviewSegment] = []
    for source in snapshot.sources:
        points = tuple(
            sorted(grouped.get(source, []), key=lambda point: point.valid_time)
        )
        # Forecast timestep does not establish the cadence of issues at a fixed lead.
        segments.append(ReviewSegment(source=source, points=points, gaps=()))
    has_values = any(
        p.state is ReviewPointState.AVAILABLE for s in segments for p in s.points
    )
    empty = not snapshot.records
    withheld = empty and query.scope.policy is ReviewPolicy.PUBLISHED_ONLY
    return ReviewSeries(
        query=query,
        state=ReviewPointState.AVAILABLE
        if has_values
        else ReviewPointState.WITHHELD
        if withheld
        else ReviewPointState.MISSING,
        reason="no_visible_published_forecasts_in_period"
        if withheld
        else "no_usable_values_in_period"
        if not has_values
        else None,
        segments=tuple(segments),
        thresholds=threshold_snapshot,
    )
