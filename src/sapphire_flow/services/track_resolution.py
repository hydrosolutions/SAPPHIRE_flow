"""Plan 151 T5: per-track resolution -- walk-back policy + per-station
completeness + ``TrackFetchResult`` (D7, D8).

Fetch/persist split (D7): :func:`resolve_candidate` is the pure fetch/validate
half -- it performs NO store I/O, only calling the injected ``fetch_candidate``
callable (T8 wraps this in a retrying Prefect task) once per walk-back
candidate. :func:`commit_track` is the serial convergence half -- it persists
ONLY complete stations' rows, reads them back, and maps final per-station
availability. Splitting them lets T8 run each as its own Prefect task
boundary and keeps this module free of any ``WeatherForecastStore`` write
until a candidate has already passed completeness (D8.3: an incomplete
candidate must never reach Postgres).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.preprocessing.converters import (
    basin_avg_to_records,
    elevation_band_to_records,
    point_forecast_to_records,
)
from sapphire_flow.services.operational_inputs import reduced_daily_step_counts
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleMode
from sapphire_flow.types.forcing_track import (
    RawFetchStatus,
    StationTrackAvailable,
    StationTrackUnavailable,
    StationUnavailableReason,
    TrackFetchResult,
)
from sapphire_flow.types.weather import (
    BasinAverageForecast,
    PointForecast,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sapphire_flow.protocols.stores import WeatherForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.forcing_track import RawFetchOutcome, ResolvedTrackRequest
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.weather import WeatherForecastRecord, WeatherForecastResult

log = structlog.get_logger(__name__)


def _floor_to_cadence(moment: UtcDatetime, cadence_hours: float) -> UtcDatetime:
    """Services-side cadence flooring (D7: walk-back is a pure ``services/``
    concern, never inside the adapter -- deliberately NOT imported from
    ``adapters/recap_gateway.py``, which has its own private mirror used only
    for the LEGACY ``resolve_latest_cycle`` path)."""
    cadence_seconds = cadence_hours * 3600.0
    elapsed = moment.timestamp()
    floored = elapsed - (elapsed % cadence_seconds)
    return ensure_utc(datetime.fromtimestamp(floored, tz=UTC))


def _forecast_result_to_records(
    station_id: StationId,
    forecast: WeatherForecastResult,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
) -> list[WeatherForecastRecord]:
    if isinstance(forecast, BasinAverageForecast):
        return basin_avg_to_records(station_id, forecast, clock, id_gen)
    if isinstance(forecast, PointForecast):
        return point_forecast_to_records(station_id, forecast, clock, id_gen)
    return elevation_band_to_records(station_id, forecast, clock, id_gen)


def _station_complete(
    records: list[WeatherForecastRecord],
    *,
    track_request: ResolvedTrackRequest,
    expected_member_ids: frozenset[int],
    issue_time: UtcDatetime,
) -> bool:
    """D8.1: per-station raw completeness. For every feature in the track,
    this station's series must reach ``fetch_horizons[f]`` steps (counted in
    the track's ``time_step`` units, after the SAME daily-bucket reduction
    assembly applies) and — for an ENSEMBLE track — carry EXACTLY
    ``expected_member_ids`` at that horizon. A SINGLE-mode track has no
    member axis; the single run's own count is the bar."""
    for feature, horizon in track_request.fetch_horizons.items():
        feature_records = [r for r in records if r.parameter == feature]
        counts = reduced_daily_step_counts(
            feature_records,
            time_step=track_request.key.time_step,
            issue_time=issue_time,
        )
        if track_request.key.ensemble_mode is EnsembleMode.SINGLE:
            if not counts or max(counts.values()) < horizon.value:
                return False
            continue

        # An ENSEMBLE track's member axis is never `None` (that is the
        # SINGLE-mode absence-of-a-member-axis case, handled above) --
        # filtering it out here keeps the comparison against
        # `expected_member_ids: frozenset[int]` type-honest rather than
        # comparing against a `set[int | None]` that can never equal it.
        present_members = frozenset(
            member_id for (_, member_id) in counts if member_id is not None
        )
        if present_members != expected_member_ids:
            return False
        for member_id in expected_member_ids:
            if counts.get((feature, member_id), 0) < horizon.value:
                return False
    return True


@dataclass(frozen=True, kw_only=True, slots=True)
class AcceptedCandidate:
    """The walk-back-accepted candidate for one track (D7/D8): the resolved
    cycle, plus per-COMPLETE-station raw records ready for persist, plus
    which in-scope stations were incomplete AT THIS CYCLE (their rows are
    discarded here, D8.3 — never returned for persist)."""

    resolved_cycle: UtcDatetime
    nwp_cycle_source: NwpCycleSource
    complete_station_records: dict[StationId, list[WeatherForecastRecord]]
    incomplete_at_cycle: frozenset[StationId]


def resolve_candidate(
    track_request: ResolvedTrackRequest,
    *,
    fetch_candidate: Callable[[UtcDatetime], RawFetchOutcome],
    expected_member_ids: frozenset[int],
    nominal_cycle_source: Callable[[UtcDatetime, UtcDatetime], NwpCycleSource],
    nominal_now: UtcDatetime,
    issue_time: UtcDatetime,
    cycle_cadence_hours: float,
    max_cycle_age_hours: float,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
) -> AcceptedCandidate | None:
    """Pure fetch/validate half — NO store I/O (D7). Walks back from the
    cadence-floored ``nominal_now``, calling ``fetch_candidate`` per
    candidate; a candidate is ACCEPTED the moment at least one in-scope
    station passes :func:`_station_complete` (D8.2 — COMPLETE iff >= 1
    station complete). Exceptions from ``fetch_candidate`` (fatal
    transport/config/auth/integrity errors, D31) propagate UNCAUGHT — this
    function classifies only the ``RawFetchOutcome`` return value, never an
    exception. Returns ``None`` if ``max_cycle_age_hours`` is exhausted with
    no candidate ever COMPLETE (walk-back exhausted — the caller reports
    ``MISSING_CONTEXT``, D3-mapping).
    """
    if cycle_cadence_hours <= 0 or max_cycle_age_hours <= 0:
        raise ValueError(
            "cycle_cadence_hours and max_cycle_age_hours must both be > 0, got "
            f"{cycle_cadence_hours!r}, {max_cycle_age_hours!r}"
        )
    candidate_cycle = _floor_to_cadence(nominal_now, cycle_cadence_hours)
    nominal_candidate = candidate_cycle
    steps = int(max_cycle_age_hours // cycle_cadence_hours) + 1

    for _ in range(steps):
        outcome = fetch_candidate(candidate_cycle)
        if outcome.status is RawFetchStatus.FETCHED:
            complete: dict[StationId, list[WeatherForecastRecord]] = {}
            incomplete: set[StationId] = set()
            for station_id, forecast in outcome.stations.items():
                records = _forecast_result_to_records(
                    station_id, forecast, clock, id_gen
                )
                if _station_complete(
                    records,
                    track_request=track_request,
                    expected_member_ids=expected_member_ids,
                    issue_time=issue_time,
                ):
                    complete[station_id] = records
                else:
                    incomplete.add(station_id)
            if complete:
                log.info(
                    "nwp.track_resolved",
                    track_features=sorted(track_request.key.features),
                    resolved_cycle=candidate_cycle.isoformat(),
                    complete_stations=len(complete),
                    incomplete_at_cycle=len(incomplete),
                )
                return AcceptedCandidate(
                    resolved_cycle=candidate_cycle,
                    nwp_cycle_source=nominal_cycle_source(
                        candidate_cycle, nominal_candidate
                    ),
                    complete_station_records=complete,
                    incomplete_at_cycle=frozenset(incomplete),
                )
            log.warning(
                "nwp.candidate_rejected",
                track_features=sorted(track_request.key.features),
                candidate_cycle=candidate_cycle.isoformat(),
                reason="no_station_complete",
            )
        else:
            log.warning(
                "nwp.candidate_rejected",
                track_features=sorted(track_request.key.features),
                candidate_cycle=candidate_cycle.isoformat(),
                reason="absent_at_cycle",
                detail=outcome.absent_detail,
            )
        candidate_cycle = ensure_utc(
            candidate_cycle - timedelta(hours=cycle_cadence_hours)
        )

    log.warning(
        "nwp.track_walkback_exhausted",
        track_features=sorted(track_request.key.features),
        nominal_cycle=nominal_candidate.isoformat(),
        max_cycle_age_hours=max_cycle_age_hours,
    )
    return None


def commit_track(
    accepted: AcceptedCandidate,
    *,
    in_scope_station_ids: frozenset[StationId],
    weather_forecast_store: WeatherForecastStore,
    nwp_source: str,
) -> TrackFetchResult:
    """Serial convergence half (D7): persist ONLY complete stations' rows,
    read them back, and map final per-station availability (D8.4). An
    in-scope station absent from ``accepted.complete_station_records``
    (either genuinely incomplete-at-cycle, D8.3, or simply never returned by
    the source) is ``StationTrackUnavailable``."""
    all_records = [
        record
        for records in accepted.complete_station_records.values()
        for record in records
    ]
    if all_records:
        weather_forecast_store.store_weather_forecasts(all_records)

    outcomes: dict[StationId, StationTrackAvailable | StationTrackUnavailable] = {}
    for station_id in in_scope_station_ids:
        if station_id in accepted.incomplete_at_cycle:
            outcomes[station_id] = StationTrackUnavailable(
                reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE
            )
            continue
        readback = weather_forecast_store.fetch_weather_forecasts(
            station_id=station_id,
            nwp_source=nwp_source,
            cycle_time=accepted.resolved_cycle,
        )
        if not readback:
            outcomes[station_id] = StationTrackUnavailable(
                reason=StationUnavailableReason.NO_DATA_AT_CYCLE
            )
            continue
        outcomes[station_id] = StationTrackAvailable(
            cycle=accepted.resolved_cycle,
            records=readback,
            provenance=accepted.nwp_cycle_source,
        )

    return TrackFetchResult(
        resolved_cycle=accepted.resolved_cycle, station_outcomes=outcomes
    )
