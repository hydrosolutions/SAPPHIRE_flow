from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from sapphire_flow.flows.ingest_recap_era5_reanalysis import (
    fetch_combined_operational_windows,
    fetch_combined_period_windows,
)
from sapphire_flow.protocols.adapters import OperationalWeatherPeriodSource
from sapphire_flow.protocols.stores import OperationalWeatherPeriodCacheStore
from sapphire_flow.services.retained_operational_weather import (
    RetainedPastRequirement,
    SnapshotSupport,
    retained_future_samples,
    retained_tail_cycle,
    snapshot_support,
)
from sapphire_flow.store.operational_weather_codec import (
    WeatherSnapshotSizeError,
    make_past_snapshot,
    verify_past_snapshot,
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
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherAttemptV2,
    FutureWeatherReceiptV2,
    FutureWeatherRequestV2,
    PastWeatherRequirementV2,
    WeatherCacheAttemptV2,
    WeatherCacheCompletionV2,
    WeatherCacheEntryV2,
    WeatherPastSnapshotV2,
    require_context,
)
from sapphire_flow.types.recap_intervals import RADIATION_PARAMETERS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

    from sapphire_flow.flows.operational_weather_horizon import CandidateHandoff
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
    from sapphire_flow.services.track_resolution import AcceptedCandidate
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
    from sapphire_flow.types.operational_weather_v2 import FutureWeatherScopeV2
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.time_grid import TimeGrid
    from sapphire_flow.types.weather_gap import WeatherGapFill
    from sapphire_flow.types.write_principal import WritePrincipal


def fatal_weather_reason(exc: Exception) -> WeatherFailureReason:
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
    past_requirement: PastWeatherRequirement | PastWeatherRequirementV2 | None
    future_requests: tuple[FutureWeatherRequest | FutureWeatherRequestV2, ...]
    retained_past_requirements: tuple[RetainedPastRequirement, ...] = ()
    retained_future_bindings: frozenset[str] = frozenset()

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
    attempt: WeatherCacheAttempt | WeatherCacheAttemptV2
    entry: WeatherCacheEntry | WeatherCacheEntryV2


class OperationalWeatherRefresh:
    """One native cycle's refresh owner; consumers receive only completed views."""

    def __init__(
        self,
        *,
        specs: tuple[StationWeatherRefresh, ...],
        cache: OperationalWeatherCacheStore | OperationalWeatherPeriodCacheStore,
        principal: WritePrincipal,
        issue_time: UtcDatetime,
        clock: Callable[[], UtcDatetime],
        id_gen: Callable[[], UUID],
        max_past_bytes: int,
        grid: TimeGrid | None = None,
        eligibility_cutoff: UtcDatetime | None = None,
        weather_store: WeatherForecastStore | None = None,
        earliest_cycle: UtcDatetime | None = None,
        max_retained_past_lead: timedelta = timedelta(hours=72),
    ) -> None:
        if len({spec.key.station_id for spec in specs}) != len(specs):
            raise ValueError("weather refresh repeats a station")
        if (
            any(spec.retained_past_requirements for spec in specs)
            and earliest_cycle is None
        ):
            raise ValueError(
                "retained weather selection requires its receipt expiry boundary"
            )
        if grid is not None:
            if eligibility_cutoff is None or not isinstance(
                cache, OperationalWeatherPeriodCacheStore
            ):
                raise ValueError(
                    "period refresh requires period cache and explicit cutoff"
                )
            require_context(grid, issue_time, eligibility_cutoff)
            for spec in specs:
                if spec.past_requirement is not None and (
                    not isinstance(spec.past_requirement, PastWeatherRequirementV2)
                    or spec.past_requirement.grid != grid
                    or spec.past_requirement.end > eligibility_cutoff
                ):
                    raise ValueError("past refresh period context mismatch")
                if any(
                    not isinstance(request, FutureWeatherRequestV2)
                    or request.grid != grid
                    or request.issue_time != issue_time
                    or request.eligibility_cutoff != eligibility_cutoff
                    for request in spec.future_requests
                ):
                    raise ValueError("future refresh period context mismatch")
        elif eligibility_cutoff is not None or any(
            isinstance(spec.past_requirement, PastWeatherRequirementV2)
            or any(
                isinstance(request, FutureWeatherRequestV2)
                for request in spec.future_requests
            )
            for spec in specs
        ):
            raise ValueError("period refresh requires an explicit grid")
        self._grid = grid
        self._cutoff = eligibility_cutoff
        self._specs = {spec.key.station_id: spec for spec in specs}
        self._cache = cache
        self._principal = principal
        self._issue_time = issue_time
        self._clock = clock
        self._id_gen = id_gen
        self._max_past_bytes = max_past_bytes
        self._attempts: dict[
            StationId, WeatherCacheAttempt | WeatherCacheAttemptV2
        ] = {}
        self._snapshots: dict[
            StationId, WeatherPastSnapshot | WeatherPastSnapshotV2
        ] = {}
        self._past: dict[StationId, WeatherLegOutcome] = {
            sid: WeatherLegOutcome(state=WeatherLegState.UNUSED)
            for sid, spec in self._specs.items()
            if spec.past_requirement is None
        }
        self._future: dict[
            FutureWeatherRequest | FutureWeatherRequestV2,
            FutureWeatherAttempt | FutureWeatherAttemptV2,
        ] = {}
        self._receipts: dict[
            FutureWeatherRequest | FutureWeatherRequestV2,
            FutureWeatherReceipt | FutureWeatherReceiptV2,
        ] = {}
        self._completions: dict[
            StationId, WeatherCacheCompletion | WeatherCacheCompletionV2
        ] = {}
        self._completed: dict[StationId, CompletedStationWeather] = {}
        self._previous: dict[StationId, WeatherCacheEntry] = {}
        self._selected: dict[StationId, WeatherPastSnapshot] = {}
        self._retained_receipts: dict[FutureWeatherRequest, FutureWeatherReceipt] = {}
        self._weather_store = weather_store
        self._earliest_cycle = earliest_cycle
        self._max_retained_past_lead = max_retained_past_lead

    def begin(self) -> None:
        if self._attempts:
            raise ValueError("weather refresh was already begun")
        for sid, spec in self._specs.items():
            if self._grid is not None:
                assert self._cutoff is not None
                self._attempts[sid] = cast(
                    "OperationalWeatherPeriodCacheStore", self._cache
                ).begin_period(
                    key=spec.key,
                    principal=self._principal,
                    attempt_id=self._id_gen(),
                    issue_time=self._issue_time,
                    started_at=self._clock(),
                    grid=self._grid,
                    eligibility_cutoff=self._cutoff,
                    future_scopes=cast(
                        "tuple[FutureWeatherRequestV2, ...]", spec.future_requests
                    ),
                )
            else:
                self._attempts[sid] = cast(
                    "OperationalWeatherCacheStore", self._cache
                ).begin(
                    key=spec.key,
                    principal=self._principal,
                    attempt_id=self._id_gen(),
                    issue_time=self._issue_time,
                    started_at=self._clock(),
                    future_scopes=cast(
                        "tuple[FutureWeatherRequest, ...]", spec.future_requests
                    ),
                )
                previous = cast("OperationalWeatherCacheStore", self._cache).fetch(
                    spec.key
                )
                if previous is not None:
                    self._previous[sid] = previous

    def fetch_past(
        self,
        *,
        adapter: OperationalWeatherSource | OperationalWeatherPeriodSource,
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

        requested_parameters = {
            sid: spec.past_requirement.parameters
            for sid, spec in self._specs.items()
            if spec.past_requirement is not None
        }
        parameters_by_station = (
            requested_parameters
            if any(
                RADIATION_PARAMETERS.intersection(names)
                for names in requested_parameters.values()
            )
            else None
        )
        try:
            if self._grid is None:
                windows = fetch_combined_operational_windows(
                    parameters_by_station=parameters_by_station,
                    adapter=cast("OperationalWeatherSource", adapter),
                    sources=sources,
                    station_store=station_store,
                    principal=self._principal,
                    audit_log_store=audit_log_store,
                    issue_time=self._issue_time,
                    start=start,
                    end=self._issue_time,
                    now=self._clock(),
                )
            else:
                if not isinstance(adapter, OperationalWeatherPeriodSource):
                    raise ValueError("period refresh requires a period-aware source")
                assert self._cutoff is not None
                pasts = [
                    spec.past_requirement
                    for spec in self._specs.values()
                    if spec.past_requirement is not None
                ]
                if start > min(past.start for past in pasts):
                    raise ValueError(
                        "period refresh start omits requested past support"
                    )
                windows = fetch_combined_period_windows(
                    parameters_by_station=parameters_by_station,
                    adapter=adapter,
                    sources=sources,
                    station_store=station_store,
                    principal=self._principal,
                    audit_log_store=audit_log_store,
                    issue_time=self._issue_time,
                    start=start,
                    end=max(past.end for past in pasts),
                    now=self._clock(),
                    grid=self._grid,
                    eligibility_cutoff=self._cutoff,
                )
            if set(windows) != {source.station_id for source in sources}:
                raise ValueError("combined weather response station set mismatch")
            snapshots: dict[StationId, WeatherPastSnapshot | WeatherPastSnapshotV2] = {}
            for sid, window in windows.items():
                if (
                    window.binding_fingerprint
                    != self._specs[sid].key.binding_fingerprint
                ):
                    raise ValueError("combined weather binding changed during refresh")
                try:
                    snapshot = make_past_snapshot(
                        window=window,
                        retrieved_at=self._clock(),
                        snapshot_id=self._id_gen(),
                        max_bytes=self._max_past_bytes,
                    )
                except WeatherSnapshotSizeError:
                    self._past[sid] = WeatherLegOutcome(
                        state=WeatherLegState.UNAVAILABLE,
                        reason=WeatherFailureReason.MISSING_SUPPORT,
                    )
                    continue
                if (
                    isinstance(snapshot, WeatherPastSnapshot)
                    and self._specs[sid].retained_past_requirements
                ):
                    chosen = self._select_snapshot(sid, snapshot)
                    if chosen is not snapshot:
                        self._past[sid] = WeatherLegOutcome(
                            state=WeatherLegState.UNAVAILABLE,
                            reason=WeatherFailureReason.MISSING_SUPPORT,
                        )
                        continue
                snapshots[sid] = snapshot
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
                    state=WeatherLegState.FATAL, reason=fatal_weather_reason(exc)
                )
            )
            raise

    def _select_snapshot(
        self, sid: StationId, fresh: WeatherPastSnapshot | None = None
    ) -> WeatherPastSnapshot | None:
        from sapphire_flow.services.selected_weather_inputs import (
            qualified_stored_weather_donors,
        )

        spec = self._specs[sid]
        previous = self._previous.get(sid)
        old = None if previous is None else previous.past_snapshot
        if not spec.retained_past_requirements:
            return None
        requests = cast("tuple[FutureWeatherRequest, ...]", spec.future_requests)
        donors: dict[tuple[str, UtcDatetime], WeatherGapFill] = {}
        if previous is not None and self._weather_store is not None:
            from dataclasses import replace

            donor_entry = replace(
                previous,
                future_receipts=tuple(
                    receipt
                    for receipt in previous.future_receipts
                    if self._earliest_cycle is None
                    or receipt.cycle_time >= self._earliest_cycle
                ),
            )
            for policy in spec.retained_past_requirements:
                stamps = {
                    policy.past.start + timedelta(days=i)
                    for i in range((policy.past.end - policy.past.start).days)
                }
                for request in requests:
                    donors.update(
                        cast(
                            "dict[tuple[str, UtcDatetime], WeatherGapFill]",
                            qualified_stored_weather_donors(
                                station_id=sid,
                                entry=donor_entry,
                                future_requirement=request,
                                store=self._weather_store,
                                ceiling=self._issue_time,
                                missing_days=stamps,
                                gap_parameters=policy.fill_parameters,
                                max_past_lead=self._max_retained_past_lead,
                            ),
                        )
                    )
        candidates: list[tuple[SnapshotSupport, WeatherPastSnapshot]] = []
        for snapshot in (fresh, old):
            if snapshot is None:
                continue
            verify_past_snapshot(snapshot, max_bytes=self._max_past_bytes)
            support = snapshot_support(
                snapshot,
                binding_fingerprint=spec.key.binding_fingerprint,
                requirements=spec.retained_past_requirements,
                future_requests=requests,
                issue_time=self._issue_time,
                max_past_lead=self._max_retained_past_lead,
                donors=donors,
            )
            if support is not SnapshotSupport.UNUSABLE:
                candidates.append((support, snapshot))
        if fresh is not None and old is not None:
            old_cycle = retained_tail_cycle(old, requests)
            fresh_cycle = retained_tail_cycle(fresh, requests)
            if old_cycle is not None and (
                fresh_cycle is None or fresh_cycle < old_cycle
            ):
                candidates = [
                    (quality, snap) for quality, snap in candidates if snap is not fresh
                ]
        chosen = max(candidates, key=lambda item: item[0], default=None)
        if chosen is not None:
            self._selected[sid] = chosen[1]
            return chosen[1]
        return None

    def retained_candidate(
        self,
        declared: Mapping[
            StationId, tuple[FutureWeatherRequest | FutureWeatherRequestV2, ...]
        ],
        *,
        earliest_cycle: UtcDatetime,
    ) -> AcceptedCandidate | None:
        from sapphire_flow.services.track_resolution import AcceptedCandidate
        from sapphire_flow.types.enums import NwpCycleSource, SpatialRepresentation
        from sapphire_flow.types.weather import WeatherForecastRecord

        if self._prior_future_covers(declared, earliest_cycle=earliest_cycle):
            return None

        selected: dict[StationId, tuple[WeatherPastSnapshot, UtcDatetime]] = {}
        for sid, requests in declared.items():
            spec = self._specs[sid]
            if not requests or any(
                isinstance(r, FutureWeatherRequestV2)
                or r.binding_fingerprint not in spec.retained_future_bindings
                for r in requests
            ):
                continue
            outcome = self._past.get(sid)
            if outcome is None or outcome.state not in (
                WeatherLegState.SUCCESS,
                WeatherLegState.UNAVAILABLE,
            ):
                continue
            snapshot = self._selected.get(sid) or self._select_snapshot(sid)
            if snapshot is None:
                continue
            cycle = retained_tail_cycle(
                snapshot, cast("tuple[FutureWeatherRequest, ...]", requests)
            )
            if cycle is None or cycle < earliest_cycle:
                continue
            selected[sid] = (snapshot, cycle)
        if not selected:
            return None
        chosen_cycle = max(cycle for _, cycle in selected.values())
        rows: dict[StationId, list[WeatherForecastRecord]] = {}
        now = self._clock()
        for sid, (snapshot, cycle) in selected.items():
            if cycle != chosen_cycle:
                continue
            samples = {
                (sample.parameter, sample.valid_time): sample
                for request in cast("tuple[FutureWeatherRequest, ...]", declared[sid])
                for sample in (
                    retained_future_samples(snapshot, request, cycle=cycle) or ()
                )
            }
            rows[sid] = [
                WeatherForecastRecord(
                    id=self._id_gen(),
                    station_id=sid,
                    nwp_source="ifs_ecmwf",
                    cycle_time=cycle,
                    valid_time=sample.valid_time,
                    parameter=sample.parameter,
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=0,
                    value=sample.value,
                    created_at=now,
                )
                for sample in samples.values()
            ]
        return AcceptedCandidate(
            resolved_cycle=chosen_cycle,
            nwp_cycle_source=NwpCycleSource.FALLBACK,
            complete_station_records=rows,
            incomplete_at_cycle=frozenset(set(declared) - set(rows)),
        )

    def _prior_future_covers(
        self,
        declared: Mapping[
            StationId, tuple[FutureWeatherRequest | FutureWeatherRequestV2, ...]
        ],
        *,
        earliest_cycle: UtcDatetime,
    ) -> bool:
        from sapphire_flow.services.operational_weather_projection import (
            future_receipt_scope,
        )

        for sid, requests in declared.items():
            entry = self._previous.get(sid)
            if entry is None:
                return False
            for request in requests:
                matching = []
                for receipt in entry.future_receipts:
                    if (
                        receipt.scope.binding_fingerprint != request.binding_fingerprint
                        or receipt.scope.source != request.source
                        or receipt.scope.issue_time > request.issue_time
                        or not earliest_cycle
                        <= receipt.cycle_time
                        <= request.issue_time
                    ):
                        continue
                    scope = future_receipt_scope(request, cycle_time=receipt.cycle_time)
                    if scope is None:
                        continue
                    wanted = {
                        (r.parameter, t, m)
                        for r in scope.requirements
                        for t in r.valid_times
                        for m in r.member_ids
                    }
                    covered = {
                        (r.parameter, t, m)
                        for r in receipt.scope.requirements
                        for t in r.valid_times
                        for m in r.member_ids
                    }
                    matching.append(wanted <= covered)
                if not any(matching):
                    return False
        return bool(declared)

    def record_retained_future(self, receipt: FutureWeatherReceipt) -> None:
        request = receipt.scope.request
        snapshot = self._selected.get(request.station_id)
        if (
            snapshot is None
            or retained_future_samples(snapshot, request, cycle=receipt.cycle_time)
            is None
        ):
            raise ValueError("retained future proof lacks selected past origin")
        verify_past_snapshot(snapshot, max_bytes=self._max_past_bytes)
        self._retained_receipts[request] = receipt

    def future_requests_for(
        self, station_id: StationId
    ) -> tuple[FutureWeatherRequest | FutureWeatherRequestV2, ...]:
        spec = self._specs.get(station_id)
        return () if spec is None else spec.future_requests

    def record_future(
        self,
        *,
        request: FutureWeatherRequest | FutureWeatherRequestV2,
        outcome: WeatherLegOutcome,
        receipt: FutureWeatherReceipt | FutureWeatherReceiptV2 | None = None,
    ) -> None:
        spec = self._specs.get(request.station_id)
        if spec is None or request not in spec.future_requests:
            raise ValueError("future result was not declared before refresh")
        if (outcome.state is WeatherLegState.SUCCESS) != (receipt is not None):
            raise ValueError("successful future result requires a receipt")
        if receipt is not None and receipt.scope.request != request:
            raise ValueError("future receipt does not match its request")
        cycle = None if receipt is None else receipt.cycle_time
        result = (
            FutureWeatherAttemptV2(scope=request, outcome=outcome, resolved_cycle=cycle)
            if isinstance(request, FutureWeatherRequestV2)
            else FutureWeatherAttempt(
                scope=request, outcome=outcome, resolved_cycle=cycle
            )
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
        requests: tuple[FutureWeatherRequest | FutureWeatherRequestV2, ...],
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
                    request,
                    (
                        FutureWeatherAttemptV2(scope=request, outcome=outcome)
                        if isinstance(request, FutureWeatherRequestV2)
                        else FutureWeatherAttempt(scope=request, outcome=outcome)
                    ),
                )

    def _finish_station(
        self, sid: StationId, *, earliest_cycle: UtcDatetime
    ) -> CompletedStationWeather:
        if sid in self._completed:
            return self._completed[sid]
        attempt = self._attempts[sid]
        if (
            self._specs[sid].retained_past_requirements
            and self._past[sid].state is WeatherLegState.UNAVAILABLE
            and sid not in self._selected
            and self._select_snapshot(sid) is None
        ):
            self._past[sid] = WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE,
                reason=WeatherFailureReason.NO_USABLE_PRODUCT,
            )
        if any(request.station_id == sid for request in self._retained_receipts):
            previous = self._previous.get(sid)
            origin = self._snapshots.get(sid) or (
                None if previous is None else previous.past_snapshot
            )
            if origin != self._selected.get(sid):
                raise ValueError(
                    "retained future origin differs from selected past snapshot"
                )
        if sid not in self._completions:
            requests = self._specs[sid].future_requests
            snapshot = self._snapshots.get(sid)
            attempts = tuple(self._future[request] for request in requests)
            receipts = tuple(
                self._receipts[request]
                for request in requests
                if request in self._receipts
            )
            if isinstance(attempt, WeatherCacheAttemptV2):
                self._completions[sid] = WeatherCacheCompletionV2(
                    finished_at=self._clock(),
                    past_outcome=self._past[sid],
                    past_snapshot=cast("WeatherPastSnapshotV2 | None", snapshot),
                    future_attempts=cast(
                        "tuple[FutureWeatherAttemptV2, ...]", attempts
                    ),
                    future_receipts=cast(
                        "tuple[FutureWeatherReceiptV2, ...]", receipts
                    ),
                )
            else:
                self._completions[sid] = WeatherCacheCompletion(
                    finished_at=self._clock(),
                    past_outcome=self._past[sid],
                    past_snapshot=cast("WeatherPastSnapshot | None", snapshot),
                    future_attempts=cast("tuple[FutureWeatherAttempt, ...]", attempts),
                    future_receipts=cast("tuple[FutureWeatherReceipt, ...]", receipts),
                    retained_future_receipts=tuple(
                        self._retained_receipts[request]
                        for request in requests
                        if request in self._retained_receipts
                    ),
                )
        if isinstance(attempt, WeatherCacheAttemptV2):
            entry = cast(
                "OperationalWeatherPeriodCacheStore", self._cache
            ).finish_period(
                attempt=attempt,
                principal=self._principal,
                earliest_cycle=earliest_cycle,
                completion=cast("WeatherCacheCompletionV2", self._completions[sid]),
            )
        else:
            entry = cast("OperationalWeatherCacheStore", self._cache).finish(
                attempt=attempt,
                principal=self._principal,
                earliest_cycle=earliest_cycle,
                completion=cast("WeatherCacheCompletion", self._completions[sid]),
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
    negotiated_candidates: dict[ForcingTrackKey, CandidateHandoff] | None = None,
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

        period_requests = [
            request
            for requests in declared.values()
            for request in requests
            if isinstance(request, FutureWeatherRequestV2)
        ]
        context = period_requests[0] if period_requests else None
        if context is not None and (
            len(period_requests) != sum(len(requests) for requests in declared.values())
            or any(
                (request.grid, request.first_period_start, request.eligibility_cutoff)
                != (
                    context.grid,
                    context.first_period_start,
                    context.eligibility_cutoff,
                )
                for request in period_requests
            )
        ):
            raise ValueError("selected track period contexts differ")

        def fetch(
            cycle: UtcDatetime,
            *,
            key: ForcingTrackKey = track.key,
            bindings: list[StationWeatherSource] = sources,
        ) -> RawFetchOutcome:
            return fetch_candidate(key, bindings, cycle)

        try:
            if negotiated_candidates is not None and track.key in negotiated_candidates:
                from sapphire_flow.flows.operational_weather_horizon import (
                    UnavailableCandidate,
                    common_prefix,
                )

                handoff = negotiated_candidates.pop(track.key)
                accepted = (
                    None if isinstance(handoff, UnavailableCandidate) else handoff
                )
                if accepted is not None and (
                    context is not None
                    or members != frozenset({0})
                    or not earliest_cycle <= accepted.resolved_cycle <= issue_time
                    or any(
                        row.member_id != 0 or row.cycle_time != accepted.resolved_cycle
                        for rows in accepted.complete_station_records.values()
                        for row in rows
                    )
                    or common_prefix(
                        accepted.complete_station_records,
                        track=track,
                        issue_time=issue_time,
                    )
                    != max(value.value for value in track.fetch_horizons.values())
                ):
                    raise WeatherProofError(
                        "negotiated candidate differs from exact request"
                    )
            else:
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
                    grid=None if context is None else context.grid,
                    first_period_start=None
                    if context is None
                    else context.first_period_start,
                    eligibility_cutoff=None
                    if context is None
                    else context.eligibility_cutoff,
                )
            retained = accepted is None and context is None
            if retained:
                accepted = owner.retained_candidate(
                    declared, earliest_cycle=earliest_cycle
                )
            result = None
            if accepted is not None:
                scopes: dict[
                    StationId, tuple[FutureWeatherScope | FutureWeatherScopeV2, ...]
                ] = {}
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
                        receipt=None if retained else receipt,
                        outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS)
                        if receipt is not None and not retained
                        else WeatherLegOutcome(
                            state=WeatherLegState.UNAVAILABLE,
                            reason=WeatherFailureReason.NO_ELIGIBLE_CYCLE
                            if accepted is None
                            else WeatherFailureReason.MISSING_SUPPORT,
                        ),
                    )
                    if retained and isinstance(receipt, FutureWeatherReceipt):
                        owner.record_retained_future(receipt)
        except Exception as exc:
            owner.fail_future(
                tuple(
                    request for requests in declared.values() for request in requests
                ),
                reason=fatal_weather_reason(exc),
            )
            raise
        results[track.key] = result
    return results
