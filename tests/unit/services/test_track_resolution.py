"""Plan 151 T5 red-first: walk-back policy + per-station completeness +
``TrackFetchResult`` (D7, D8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.adapters.recap_gateway import RecapTransientError
from sapphire_flow.services.track_resolution import (
    commit_track,
    resolve_candidate,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    NwpCycleSource,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_track import (
    FeatureName,
    ForcingTrackKey,
    FutureSteps,
    RawFetchOutcome,
    RawFetchStatus,
    ResolvedTrackRequest,
    StationTrackAvailable,
    StationTrackUnavailable,
    StationUnavailableReason,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import BasinAverageForecast, WeatherForecastRecord
from tests.fakes.fake_stores import FakeWeatherForecastStore

_STATION_A = StationId(UUID(int=1))
_STATION_B = StationId(UUID(int=2))
_NWP_SOURCE = "ecmwf_ifs"
_STEP = timedelta(hours=24)
_NOMINAL_NOW = ensure_utc(datetime(2026, 1, 10, 6, tzinfo=UTC))
_ISSUE = _NOMINAL_NOW
_CADENCE_HOURS = 6.0


def _clock() -> object:
    return _NOMINAL_NOW


def _id_gen() -> UUID:
    return uuid4()


def _always_primary(candidate: object, nominal: object) -> NwpCycleSource:
    return NwpCycleSource.PRIMARY if candidate == nominal else NwpCycleSource.FALLBACK


def _track_request(
    *, feature_steps: dict[str, int], ensemble_mode: EnsembleMode = EnsembleMode.SINGLE
) -> ResolvedTrackRequest:
    horizons = {
        FeatureName(name): FutureSteps(value=steps)
        for name, steps in feature_steps.items()
    }
    key = ForcingTrackKey(
        nwp_source=_NWP_SOURCE,
        ensemble_mode=ensemble_mode,
        time_step=_STEP,
        spatial_representation=SpatialRepresentation.BASIN_AVERAGE,
        features=frozenset(horizons),
    )
    return ResolvedTrackRequest(key=key, fetch_horizons=horizons, assignments=())


def _forecast(
    station_id: StationId,
    cycle: object,
    *,
    feature_days: dict[str, int],
    member_ids: list[int | None] | None = None,
) -> BasinAverageForecast:
    member_ids = [None] if member_ids is None else member_ids
    rows: list[dict[str, object]] = []
    for feature, n_days in feature_days.items():
        for day in range(1, n_days + 1):
            valid_time = ensure_utc(cycle + timedelta(days=day))
            for member_id in member_ids:
                rows.append(
                    {
                        "valid_time": valid_time,
                        "parameter": feature,
                        "member_id": member_id,
                        "value": 1.0,
                    }
                )
    return BasinAverageForecast(
        nwp_source=_NWP_SOURCE,
        cycle_time=cycle,
        values=pl.DataFrame(rows)
        if rows
        else pl.DataFrame(
            schema={
                "valid_time": pl.Datetime("us", "UTC"),
                "parameter": pl.Utf8,
                "member_id": pl.Int64,
                "value": pl.Float64,
            }
        ),
    )


def test_incomplete_candidate_walks_back_and_never_persists() -> None:
    """Fails today: no `resolve_candidate`/`commit_track` exist at all
    (Plan 151 T5 is net-new). The FRESHEST candidate is short (1 day, needs
    2); one cycle back is complete. Must walk back — and the incomplete
    candidate's rows must never reach the store."""
    track_request = _track_request(feature_steps={"precip": 2})
    store = FakeWeatherForecastStore()
    fresh = _NOMINAL_NOW
    older = ensure_utc(fresh - timedelta(hours=_CADENCE_HOURS))
    calls: list[object] = []

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        calls.append(cycle)
        if cycle == fresh:
            forecast = _forecast(_STATION_A, cycle, feature_days={"precip": 1})
        else:
            forecast = _forecast(_STATION_A, cycle, feature_days={"precip": 2})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: forecast}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=24.0,
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is not None
    assert accepted.resolved_cycle == older
    assert calls == [fresh, older]

    result = commit_track(
        accepted,
        in_scope_station_ids=frozenset({_STATION_A}),
        weather_forecast_store=store,
        nwp_source=_NWP_SOURCE,
        track_features=frozenset({"precip"}),
    )
    assert isinstance(result.station_outcomes[_STATION_A], StationTrackAvailable)
    # The FRESH (rejected) candidate's rows must never have reached the store.
    assert all(r.cycle_time != fresh for r in store._records)


def test_uniform_partial_member_set_rejected() -> None:
    """The exact-member-set gate rejects a uniform-but-partial ensemble
    (unlike the legacy `nwp_coverage.py` identical-set-across-features
    check, which would accept a uniform 5-of-51 set)."""
    track_request = _track_request(
        feature_steps={"precip": 2}, ensemble_mode=EnsembleMode.ENSEMBLE
    )
    expected = frozenset(range(51))

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        # Only members 0-4 present -- uniform across the (single) feature,
        # but short of the expected 51.
        forecast = _forecast(
            _STATION_A, cycle, feature_days={"precip": 2}, member_ids=list(range(5))
        )
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: forecast}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=expected,
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=6.0,  # exactly one candidate -- no walk-back room
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is None  # walk-back exhausted -- MISSING_CONTEXT


def test_expected_member_ids_is_source_derived_21_and_51_with_no_validator_change() -> (
    None
):
    """The same `_station_complete` gate validates a 21-member (ICON-CH2-EPS)
    and a 51-member (ECMWF-IFS) source correctly with zero code change --
    `expected_member_ids` is the only thing that varies (Ruling 1)."""
    for member_count in (21, 51):
        track_request = _track_request(
            feature_steps={"precip": 2}, ensemble_mode=EnsembleMode.ENSEMBLE
        )
        expected = frozenset(range(member_count))

        def fetch_candidate(
            cycle: object, member_count: int = member_count
        ) -> RawFetchOutcome:
            forecast = _forecast(
                _STATION_A,
                cycle,
                feature_days={"precip": 2},
                member_ids=list(range(member_count)),
            )
            return RawFetchOutcome(
                status=RawFetchStatus.FETCHED,
                cycle=cycle,
                stations={_STATION_A: forecast},
            )

        accepted = resolve_candidate(
            track_request,
            fetch_candidate=fetch_candidate,
            expected_member_ids=expected,
            nominal_cycle_source=_always_primary,
            nominal_now=_NOMINAL_NOW,
            issue_time=_ISSUE,
            cycle_cadence_hours=_CADENCE_HOURS,
            max_cycle_age_hours=6.0,
            clock=_clock,
            id_gen=_id_gen,
        )
        assert accepted is not None, f"member_count={member_count} should COMPLETE"


def test_one_cycle_per_track_short_candidate_rejected_for_the_group_max() -> None:
    """A 5-day assignment and a 10-day sibling share a track (fetch_horizons
    is the group MAX per feature, D5). A candidate covering only 5 days for
    'temp' is REJECTED even though it would satisfy 'precip' alone, and
    walk-back continues to a candidate covering both."""
    track_request = _track_request(feature_steps={"precip": 2, "temp": 10})
    fresh = _NOMINAL_NOW
    older = ensure_utc(fresh - timedelta(hours=_CADENCE_HOURS))

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        if cycle == fresh:
            forecast = _forecast(
                _STATION_A, cycle, feature_days={"precip": 2, "temp": 5}
            )
        else:
            forecast = _forecast(
                _STATION_A, cycle, feature_days={"precip": 2, "temp": 10}
            )
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: forecast}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=24.0,
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is not None
    assert accepted.resolved_cycle == older


def test_partial_station_beside_complete_sibling_drops_before_persist() -> None:
    """Station A is complete; station B returns only a short series. B's rows
    must NOT reach the store (D8.3) -- asserted against the STORE, not just
    the outcome map."""
    track_request = _track_request(feature_steps={"precip": 2})
    store = FakeWeatherForecastStore()

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        complete = _forecast(_STATION_A, cycle, feature_days={"precip": 2})
        short = _forecast(_STATION_B, cycle, feature_days={"precip": 1})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED,
            cycle=cycle,
            stations={_STATION_A: complete, _STATION_B: short},
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=6.0,
        clock=_clock,
        id_gen=_id_gen,
    )
    assert accepted is not None
    assert _STATION_B in accepted.incomplete_at_cycle
    assert _STATION_A not in accepted.incomplete_at_cycle

    result = commit_track(
        accepted,
        in_scope_station_ids=frozenset({_STATION_A, _STATION_B}),
        weather_forecast_store=store,
        nwp_source=_NWP_SOURCE,
        track_features=frozenset({"precip"}),
    )

    assert isinstance(result.station_outcomes[_STATION_A], StationTrackAvailable)
    assert result.station_outcomes[_STATION_B] == StationTrackUnavailable(
        reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE
    )
    # The hard proof: B's rows never reached the store at all.
    assert all(r.station_id != _STATION_B for r in store._records)


def test_no_station_complete_is_absent_incomplete_and_walks_back() -> None:
    track_request = _track_request(feature_steps={"precip": 2})
    calls: list[object] = []

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        calls.append(cycle)
        short = _forecast(_STATION_A, cycle, feature_days={"precip": 1})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: short}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=18.0,
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is None
    assert len(calls) == 4  # 18h / 6h + 1


def test_exceeding_max_cycle_age_hours_yields_no_resolved_cycle() -> None:
    track_request = _track_request(feature_steps={"precip": 2})

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        return RawFetchOutcome(
            status=RawFetchStatus.ABSENT_AT_CYCLE, cycle=cycle, stations={}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=6.0,
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is None


def test_readback_maps_per_station_availability_within_same_accepted_cycle() -> None:
    """Post-readback, station A yields available and sibling B unavailable —
    B never appeared in the fetch at all (D8.4) — both within the SAME
    accepted cycle."""
    track_request = _track_request(feature_steps={"precip": 2})
    store = FakeWeatherForecastStore()

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        forecast = _forecast(_STATION_A, cycle, feature_days={"precip": 2})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: forecast}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=6.0,
        clock=_clock,
        id_gen=_id_gen,
    )
    assert accepted is not None

    result = commit_track(
        accepted,
        in_scope_station_ids=frozenset({_STATION_A, _STATION_B}),
        weather_forecast_store=store,
        nwp_source=_NWP_SOURCE,
        track_features=frozenset({"precip"}),
    )

    assert result.resolved_cycle == accepted.resolved_cycle
    a_outcome = result.station_outcomes[_STATION_A]
    assert isinstance(a_outcome, StationTrackAvailable)
    assert a_outcome.cycle == accepted.resolved_cycle
    assert result.station_outcomes[_STATION_B] == StationTrackUnavailable(
        reason=StationUnavailableReason.NO_DATA_AT_CYCLE
    )


def _staggered_member_forecast(
    station_id: StationId,
    cycle: object,
    *,
    feature: str,
    days_by_member: dict[int, list[int]],
) -> BasinAverageForecast:
    """Each member gets its OWN list of day offsets -- unlike ``_forecast``,
    which applies the SAME day range to every member."""
    rows: list[dict[str, object]] = []
    for member_id, days in days_by_member.items():
        for day in days:
            valid_time = ensure_utc(cycle + timedelta(days=day))
            rows.append(
                {
                    "valid_time": valid_time,
                    "parameter": feature,
                    "member_id": member_id,
                    "value": 1.0,
                }
            )
    return BasinAverageForecast(
        nwp_source=_NWP_SOURCE, cycle_time=cycle, values=pl.DataFrame(rows)
    )


def test_transient_error_walks_back_to_older_complete_candidate() -> None:
    """Review fold-in (major): a typed `RecapTransientError` on the freshest
    candidate must NOT abort walk-back -- it is classified TRANSIENT and the
    resolver tries the next older cycle, exactly like an ABSENT_AT_CYCLE
    candidate. Fails today: the exception propagates uncaught out of
    `resolve_candidate` and no older candidate is ever tried."""
    track_request = _track_request(feature_steps={"precip": 2})
    fresh = _NOMINAL_NOW
    older = ensure_utc(fresh - timedelta(hours=_CADENCE_HOURS))
    calls: list[object] = []

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        calls.append(cycle)
        if cycle == fresh:
            raise RecapTransientError("connection reset")
        forecast = _forecast(_STATION_A, cycle, feature_days={"precip": 2})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: forecast}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=24.0,
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is not None
    assert accepted.resolved_cycle == older
    assert calls == [fresh, older]


def test_fatal_auth_error_still_propagates_uncaught() -> None:
    """The transient carve-out must not widen to every exception -- a
    non-transient typed error (anything other than `RecapTransientError`)
    still propagates uncaught, exactly as D31 requires."""

    class _FatalError(Exception):
        pass

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        raise _FatalError("misconfigured HRU")

    with pytest.raises(_FatalError):
        resolve_candidate(
            _track_request(feature_steps={"precip": 2}),
            fetch_candidate=fetch_candidate,
            expected_member_ids=frozenset({0}),
            nominal_cycle_source=_always_primary,
            nominal_now=_NOMINAL_NOW,
            issue_time=_ISSUE,
            cycle_cadence_hours=_CADENCE_HOURS,
            max_cycle_age_hours=24.0,
            clock=_clock,
            id_gen=_id_gen,
        )


def test_missing_polygon_column_station_gets_specific_reason() -> None:
    """D27: a station the adapter excluded via `missing_polygon_column` must
    resolve to the SPECIFIC `MISSING_POLYGON_COLUMN` reason, not the generic
    `NO_DATA_AT_CYCLE` every other never-returned station gets. Fails today:
    `AcceptedCandidate` has no field carrying the set through, so
    `commit_track` cannot tell the two cases apart."""
    track_request = _track_request(feature_steps={"precip": 2})
    store = FakeWeatherForecastStore()

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        complete = _forecast(_STATION_A, cycle, feature_days={"precip": 2})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED,
            cycle=cycle,
            stations={_STATION_A: complete},
            missing_polygon_column=frozenset({_STATION_B}),
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=6.0,
        clock=_clock,
        id_gen=_id_gen,
    )
    assert accepted is not None
    assert _STATION_B in accepted.missing_polygon_column

    result = commit_track(
        accepted,
        in_scope_station_ids=frozenset({_STATION_A, _STATION_B}),
        weather_forecast_store=store,
        nwp_source=_NWP_SOURCE,
        track_features=frozenset({"precip"}),
    )
    assert result.station_outcomes[_STATION_B] == StationTrackUnavailable(
        reason=StationUnavailableReason.MISSING_POLYGON_COLUMN
    )


def test_commit_track_does_not_resurrect_unrelated_preexisting_rows() -> None:
    """Review fold-in (major): station B never appeared in this candidate's
    `.stations` at all (not complete, not incomplete-at-cycle) -- yet the
    store already holds UNRELATED rows for (B, nwp_source, this cycle) from
    an earlier write (a different track, or a legacy path). `commit_track`
    must classify B as unavailable WITHOUT reading those rows back as if
    this candidate had produced them. Fails today: readback is keyed only
    on (station_id, nwp_source, cycle_time), so it "resurrects" B as
    `StationTrackAvailable`."""
    track_request = _track_request(feature_steps={"precip": 2})
    store = FakeWeatherForecastStore()

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        complete = _forecast(_STATION_A, cycle, feature_days={"precip": 2})
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: complete}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=frozenset({0}),
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=6.0,
        clock=_clock,
        id_gen=_id_gen,
    )
    assert accepted is not None
    assert _STATION_B not in accepted.complete_station_records
    assert _STATION_B not in accepted.incomplete_at_cycle

    # Unrelated pre-existing rows for B at the SAME (nwp_source, cycle) --
    # e.g. a different track's earlier write. Never returned by THIS
    # candidate.
    store._records.append(
        WeatherForecastRecord(
            id=uuid4(),
            station_id=_STATION_B,
            nwp_source=_NWP_SOURCE,
            cycle_time=accepted.resolved_cycle,
            valid_time=ensure_utc(accepted.resolved_cycle + timedelta(days=1)),
            parameter="precip",
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=None,
            value=1.0,
            created_at=_NOMINAL_NOW,
        )
    )

    result = commit_track(
        accepted,
        in_scope_station_ids=frozenset({_STATION_A, _STATION_B}),
        weather_forecast_store=store,
        nwp_source=_NWP_SOURCE,
        track_features=frozenset({"precip"}),
    )

    assert result.station_outcomes[_STATION_B] == StationTrackUnavailable(
        reason=StationUnavailableReason.NO_DATA_AT_CYCLE
    )


def test_staggered_member_valid_times_rejects_candidate_and_walks_back() -> None:
    """Review fold-in (blocker): member 0 covers days 1-2 and member 1
    covers days 3-4 -- each individually satisfies a two-step COUNT, but
    they share NO common valid_time, so downstream assembly's earliest-N cap
    would retain only days 1-2 and silently drop member 1 entirely. The
    candidate must be REJECTED (not COMPLETE) and walk-back must continue to
    an older, genuinely-common-timestamp candidate. Fails today:
    count-based `_station_complete` accepts the freshest (staggered)
    candidate outright."""
    track_request = _track_request(
        feature_steps={"precip": 2}, ensemble_mode=EnsembleMode.ENSEMBLE
    )
    expected = frozenset({0, 1})
    fresh = _NOMINAL_NOW
    older = ensure_utc(fresh - timedelta(hours=_CADENCE_HOURS))

    def fetch_candidate(cycle: object) -> RawFetchOutcome:
        if cycle == fresh:
            forecast = _staggered_member_forecast(
                _STATION_A,
                cycle,
                feature="precip",
                days_by_member={0: [1, 2], 1: [3, 4]},
            )
        else:
            forecast = _staggered_member_forecast(
                _STATION_A,
                cycle,
                feature="precip",
                days_by_member={0: [1, 2], 1: [1, 2]},
            )
        return RawFetchOutcome(
            status=RawFetchStatus.FETCHED, cycle=cycle, stations={_STATION_A: forecast}
        )

    accepted = resolve_candidate(
        track_request,
        fetch_candidate=fetch_candidate,
        expected_member_ids=expected,
        nominal_cycle_source=_always_primary,
        nominal_now=_NOMINAL_NOW,
        issue_time=_ISSUE,
        cycle_cadence_hours=_CADENCE_HOURS,
        max_cycle_age_hours=24.0,
        clock=_clock,
        id_gen=_id_gen,
    )

    assert accepted is not None
    assert accepted.resolved_cycle == older
