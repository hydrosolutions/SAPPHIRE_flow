from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.flows.ingest_recap_era5_reanalysis import (
    fetch_combined_operational_windows,
)
from sapphire_flow.store.operational_weather_codec import (
    WeatherSnapshotSizeError,
    make_past_snapshot,
)
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherReceipt,
    FutureWeatherRequest,
    PastWeatherRequirement,
    WeatherCacheAttempt,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherFailureReason,
    WeatherLegOutcome,
    WeatherLegState,
    WeatherPastSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from sapphire_flow.protocols.adapters import (
        CandidateAwareForecastSource,
        OperationalWeatherSource,
    )
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        OperationalWeatherCacheStore,
        StationStore,
        WeatherForecastStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forcing_track import (
        ForcingRequired,
        ForcingResolutionPolicy,
        ForcingTrackKey,
        RawFetchOutcome,
        TrackFetchResult,
    )
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.operational_weather import FutureWeatherScope
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.write_principal import WritePrincipal


def _fatal_weather_reason(exc: Exception) -> WeatherFailureReason:
    from sqlalchemy.exc import SQLAlchemyError

    from sapphire_flow.adapters.recap_gateway import (
        GatewayResolutionError,
        RecapAuthError,
        RecapConfigurationError,
        RecapPayloadIntegrityError,
    )
    from sapphire_flow.exceptions import (
        ConfigurationError,
        StoreError,
        TenantIsolationError,
    )
    from sapphire_flow.services.operational_weather_proof import WeatherProofError

    if isinstance(exc, (RecapAuthError, TenantIsolationError)):
        return WeatherFailureReason.AUTHORIZATION
    if isinstance(
        exc,
        (
            ConfigurationError,
            RecapConfigurationError,
            GatewayResolutionError,
            WeatherSnapshotSizeError,
        ),
    ):
        return WeatherFailureReason.CONFIGURATION
    if isinstance(exc, (RecapPayloadIntegrityError, WeatherProofError, ValueError)):
        return WeatherFailureReason.INTEGRITY
    if isinstance(exc, (StoreError, SQLAlchemyError)):
        return WeatherFailureReason.STORAGE
    return WeatherFailureReason.PARENT_FAILURE


@dataclass(frozen=True, kw_only=True, slots=True)
class StationWeatherRefresh:
    key: WeatherCacheKey
    source: StationWeatherSource
    past_requirement: PastWeatherRequirement | None
    future_requests: tuple[FutureWeatherRequest, ...]

    def __post_init__(self) -> None:
        if self.source.station_id != self.key.station_id or any(
            request.station_id != self.key.station_id
            for request in self.future_requests
        ):
            raise ValueError("weather refresh station identity mismatch")
        if len(set(self.future_requests)) != len(self.future_requests):
            raise ValueError("weather refresh repeats a future request")


@dataclass(frozen=True, kw_only=True, slots=True)
class CompletedStationWeather:
    attempt: WeatherCacheAttempt
    entry: WeatherCacheEntry


class OperationalWeatherRefresh:
    """One native cycle's refresh owner; consumers receive only completed views."""

    def __init__(
        self,
        *,
        specs: tuple[StationWeatherRefresh, ...],
        cache: OperationalWeatherCacheStore,
        principal: WritePrincipal,
        issue_time: UtcDatetime,
        clock: Callable[[], UtcDatetime],
        id_gen: Callable[[], UUID],
        max_past_bytes: int,
    ) -> None:
        if len({spec.key.station_id for spec in specs}) != len(specs):
            raise ValueError("weather refresh repeats a station")
        self._specs = {spec.key.station_id: spec for spec in specs}
        self._cache = cache
        self._principal = principal
        self._issue_time = issue_time
        self._clock = clock
        self._id_gen = id_gen
        self._max_past_bytes = max_past_bytes
        self._attempts: dict[StationId, WeatherCacheAttempt] = {}
        self._snapshots: dict[StationId, WeatherPastSnapshot] = {}
        self._past: dict[StationId, WeatherLegOutcome] = {
            sid: WeatherLegOutcome(state=WeatherLegState.UNUSED)
            for sid, spec in self._specs.items()
            if spec.past_requirement is None
        }
        self._future: dict[FutureWeatherRequest, FutureWeatherAttempt] = {}
        self._receipts: dict[FutureWeatherRequest, FutureWeatherReceipt] = {}
        self._completions: dict[StationId, WeatherCacheCompletion] = {}
        self._completed: dict[StationId, CompletedStationWeather] = {}

    def begin(self) -> None:
        if self._attempts:
            raise ValueError("weather refresh was already begun")
        for sid, spec in self._specs.items():
            self._attempts[sid] = self._cache.begin(
                key=spec.key,
                principal=self._principal,
                attempt_id=self._id_gen(),
                issue_time=self._issue_time,
                started_at=self._clock(),
                future_scopes=spec.future_requests,
            )

    def fetch_past(
        self,
        *,
        adapter: OperationalWeatherSource,
        station_store: StationStore,
        audit_log_store: AuditLogStore | None,
        start: UtcDatetime,
    ) -> None:
        sources = [
            spec.source
            for spec in self._specs.values()
            if spec.past_requirement is not None
        ]
        if not sources:
            return
        if set(self._attempts) != set(self._specs):
            raise ValueError("all weather attempts must begin before provider access")
        from sapphire_flow.adapters.recap_gateway import (
            RecapDataUnavailableError,
            RecapTransientError,
        )

        try:
            windows = fetch_combined_operational_windows(
                adapter=adapter,
                sources=sources,
                station_store=station_store,
                principal=self._principal,
                audit_log_store=audit_log_store,
                issue_time=self._issue_time,
                start=start,
                end=self._issue_time,
                now=self._clock(),
            )
            if set(windows) != {source.station_id for source in sources}:
                raise ValueError("combined weather response station set mismatch")
            snapshots = {
                sid: make_past_snapshot(
                    window=window,
                    retrieved_at=self._clock(),
                    snapshot_id=self._id_gen(),
                    max_bytes=self._max_past_bytes,
                )
                for sid, window in windows.items()
            }
            if any(
                snapshot.window.binding_fingerprint
                != self._specs[sid].key.binding_fingerprint
                for sid, snapshot in snapshots.items()
            ):
                raise ValueError("combined weather binding changed during refresh")
            self._snapshots.update(snapshots)
            self._past.update(
                {
                    sid: WeatherLegOutcome(state=WeatherLegState.SUCCESS)
                    for sid in snapshots
                }
            )
        except (RecapDataUnavailableError, RecapTransientError):
            raise
        except Exception as exc:
            self.fail_past(
                WeatherLegOutcome(
                    state=WeatherLegState.FATAL, reason=_fatal_weather_reason(exc)
                )
            )
            raise

    def future_requests_for(
        self, station_id: StationId
    ) -> tuple[FutureWeatherRequest, ...]:
        spec = self._specs.get(station_id)
        return () if spec is None else spec.future_requests

    def record_future(
        self,
        *,
        request: FutureWeatherRequest,
        outcome: WeatherLegOutcome,
        receipt: FutureWeatherReceipt | None = None,
    ) -> None:
        spec = self._specs.get(request.station_id)
        if spec is None or request not in spec.future_requests:
            raise ValueError("future result was not declared before refresh")
        if (outcome.state is WeatherLegState.SUCCESS) != (receipt is not None):
            raise ValueError("successful future result requires a receipt")
        if receipt is not None and receipt.scope.request != request:
            raise ValueError("future receipt does not match its request")
        result = FutureWeatherAttempt(
            scope=request,
            outcome=outcome,
            resolved_cycle=None if receipt is None else receipt.cycle_time,
        )
        previous = self._future.get(request)
        if previous is not None and previous != result:
            raise ValueError("weather refresh cannot replace a terminal future result")
        if receipt is not None:
            self._receipts[request] = receipt
        self._future[request] = result

    def fail_past(self, outcome: WeatherLegOutcome) -> None:
        if outcome.state not in (
            WeatherLegState.FATAL,
            WeatherLegState.UNAVAILABLE,
            WeatherLegState.NOT_ATTEMPTED,
        ):
            raise ValueError("unfinished past weather requires an explicit failure")
        for sid in self._specs:
            self._past.setdefault(sid, outcome)

    def fail_future(
        self,
        requests: tuple[FutureWeatherRequest, ...],
        *,
        reason: WeatherFailureReason,
    ) -> None:
        for request in requests:
            if request not in self._future:
                self.record_future(
                    request=request,
                    outcome=WeatherLegOutcome(
                        state=WeatherLegState.FATAL, reason=reason
                    ),
                )

    def fail_remaining(self, outcome: WeatherLegOutcome) -> None:
        if outcome.state not in (
            WeatherLegState.FATAL,
            WeatherLegState.UNAVAILABLE,
            WeatherLegState.NOT_ATTEMPTED,
        ):
            raise ValueError("unfinished weather legs require an explicit failure")
        for sid, spec in self._specs.items():
            self._past.setdefault(sid, outcome)
            for request in spec.future_requests:
                self._future.setdefault(
                    request, FutureWeatherAttempt(scope=request, outcome=outcome)
                )

    def _finish_station(
        self, sid: StationId, *, earliest_cycle: UtcDatetime
    ) -> CompletedStationWeather:
        if sid in self._completed:
            return self._completed[sid]
        attempt = self._attempts[sid]
        if sid not in self._completions:
            requests = self._specs[sid].future_requests
            self._completions[sid] = WeatherCacheCompletion(
                finished_at=self._clock(),
                past_outcome=self._past[sid],
                past_snapshot=self._snapshots.get(sid),
                future_attempts=tuple(self._future[request] for request in requests),
                future_receipts=tuple(
                    self._receipts[request]
                    for request in requests
                    if request in self._receipts
                ),
            )
        entry = self._cache.finish(
            attempt=attempt,
            principal=self._principal,
            earliest_cycle=earliest_cycle,
            completion=self._completions[sid],
        )
        completed = CompletedStationWeather(attempt=attempt, entry=entry)
        self._completed[sid] = completed
        return completed

    def finish(
        self, *, earliest_cycle: UtcDatetime
    ) -> Mapping[StationId, CompletedStationWeather]:
        from types import MappingProxyType

        self.fail_remaining(
            WeatherLegOutcome(
                state=WeatherLegState.NOT_ATTEMPTED,
                reason=WeatherFailureReason.PARENT_FAILURE,
            )
        )
        for sid in self._attempts:
            self._finish_station(sid, earliest_cycle=earliest_cycle)
        return MappingProxyType(dict(self._completed))

    def finalize_after_failure(
        self, *, earliest_cycle: UtcDatetime
    ) -> tuple[Exception, ...]:
        self.fail_remaining(
            WeatherLegOutcome(
                state=WeatherLegState.NOT_ATTEMPTED,
                reason=WeatherFailureReason.PARENT_FAILURE,
            )
        )
        failures: list[Exception] = []
        for sid in self._attempts:
            try:
                self._finish_station(sid, earliest_cycle=earliest_cycle)
            except Exception as exc:
                # Each station owns its transaction; another failure must not
                # prevent an independently reachable station from finalizing.
                failures.append(exc)
        return tuple(failures)


def resolve_selected_weather_tracks(
    *,
    projections: tuple[ForcingRequired, ...],
    forecast_bindings: Mapping[StationId, StationWeatherSource],
    adapter: CandidateAwareForecastSource,
    policy: ForcingResolutionPolicy,
    issue_time: UtcDatetime,
    earliest_cycle: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    store: WeatherForecastStore,
    owner: OperationalWeatherRefresh,
    fetch_candidate: Callable[
        [ForcingTrackKey, list[StationWeatherSource], UtcDatetime], RawFetchOutcome
    ],
) -> dict[ForcingTrackKey, TrackFetchResult | None]:
    from sapphire_flow.services.operational_weather_projection import (
        future_receipt_scope,
    )
    from sapphire_flow.services.operational_weather_proof import WeatherProofError
    from sapphire_flow.services.track_projection import resolve_tracks
    from sapphire_flow.services.track_resolution import commit_track, resolve_candidate
    from sapphire_flow.types.enums import FutureWeatherPolicy, NwpCycleSource

    results: dict[ForcingTrackKey, TrackFetchResult | None] = {}
    for track in resolve_tracks(list(projections)):
        station_ids = frozenset(item.assignment[0] for item in track.assignments)
        sources = [forecast_bindings[sid] for sid in sorted(station_ids, key=str)]
        members = adapter.expected_member_ids(track.key)
        declared = {
            sid: tuple(
                request
                for request in owner.future_requests_for(sid)
                if request.source == track.key.nwp_source
                and frozenset(item.parameter for item in request.requirements)
                == track.key.features
                and all(
                    frozenset(item.member_ids) == members
                    for item in request.requirements
                )
            )
            for sid in station_ids
        }

        def fetch(
            cycle: UtcDatetime,
            *,
            key: ForcingTrackKey = track.key,
            bindings: list[StationWeatherSource] = sources,
        ) -> RawFetchOutcome:
            return fetch_candidate(key, bindings, cycle)

        try:
            accepted = resolve_candidate(
                track,
                fetch_candidate=fetch,
                expected_member_ids=members,
                nominal_cycle_source=lambda cycle, nominal: (
                    NwpCycleSource.PRIMARY
                    if cycle == nominal
                    else NwpCycleSource.FALLBACK
                ),
                nominal_now=issue_time,
                issue_time=issue_time,
                cycle_cadence_hours=policy.cycle_cadence_hours,
                max_cycle_age_hours=policy.max_cycle_age_hours,
                clock=clock,
                id_gen=id_gen,
                weather_policy=FutureWeatherPolicy.RECAP_COMPLETE_DAILY,
                earliest_cycle=earliest_cycle,
            )
            result = None
            if accepted is not None:
                scopes: dict[StationId, tuple[FutureWeatherScope, ...]] = {}
                for sid in accepted.complete_station_records:
                    station_scopes = tuple(
                        future_receipt_scope(
                            request, cycle_time=accepted.resolved_cycle
                        )
                        for request in declared[sid]
                    )
                    if not station_scopes or any(
                        scope is None for scope in station_scopes
                    ):
                        raise WeatherProofError(
                            "resolved track does not cover declared weather requests"
                        )
                    scopes[sid] = tuple(
                        scope for scope in station_scopes if scope is not None
                    )
                result = commit_track(
                    accepted,
                    in_scope_station_ids=station_ids,
                    weather_forecast_store=store,
                    nwp_source=track.key.nwp_source,
                    track_features=track.key.features,
                    receipt_scopes=scopes,
                    receipt_validated_at=clock(),
                )
            receipts = (
                {}
                if result is None
                else {
                    receipt.scope.request: receipt
                    for receipt in result.weather_receipts
                }
            )
            for requests in declared.values():
                for request in requests:
                    receipt = receipts.get(request)
                    owner.record_future(
                        request=request,
                        receipt=receipt,
                        outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS)
                        if receipt is not None
                        else WeatherLegOutcome(
                            state=WeatherLegState.UNAVAILABLE,
                            reason=WeatherFailureReason.NO_ELIGIBLE_CYCLE
                            if accepted is None
                            else WeatherFailureReason.MISSING_SUPPORT,
                        ),
                    )
        except Exception as exc:
            owner.fail_future(
                tuple(
                    request for requests in declared.values() for request in requests
                ),
                reason=_fatal_weather_reason(exc),
            )
            raise
        results[track.key] = result
    return results
