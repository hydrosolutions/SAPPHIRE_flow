"""Plan 151 T1 red-first: forcing-track domain types (D1)."""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from sapphire_flow.services.run_station_forecast import AssignmentFailureCause
from sapphire_flow.types.enums import EnsembleMode, SpatialRepresentation
from sapphire_flow.types.forcing_track import (
    CandidateFetchResult,
    CandidateFetchStatus,
    FeatureName,
    ForcingRequired,
    ForcingTrackKey,
    FutureSteps,
    NoForcingRequired,
    RawFetchOutcome,
    RawFetchStatus,
    ResolvedTrackRequest,
    StationTrackAvailable,
    StationTrackUnavailable,
    StationUnavailableReason,
    TrackFetchResult,
    TrackProjection,
)


class TestFutureSteps:
    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            FutureSteps(value=0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            FutureSteps(value=-1)

    def test_positive_constructs(self) -> None:
        assert FutureSteps(value=5).value == 5


def _key(**overrides: object) -> ForcingTrackKey:
    defaults: dict[str, object] = {
        "nwp_source": "ifs_ecmwf",
        "ensemble_mode": EnsembleMode.ENSEMBLE,
        "time_step": timedelta(hours=6),
        "spatial_representation": SpatialRepresentation.BASIN_AVERAGE,
        "features": frozenset({FeatureName("tp"), FeatureName("t_2m")}),
    }
    defaults.update(overrides)
    return ForcingTrackKey(**defaults)  # type: ignore[arg-type]


class TestForcingTrackKey:
    def test_hashable_usable_as_dict_key(self) -> None:
        d = {_key(): "value"}
        assert d[_key()] == "value"

    def test_order_independent_feature_set_equal_and_hash_equal(self) -> None:
        a = _key(features=frozenset({FeatureName("tp"), FeatureName("t_2m")}))
        b = _key(features=frozenset({FeatureName("t_2m"), FeatureName("tp")}))
        assert a == b
        assert hash(a) == hash(b)

    def test_carries_no_horizon_field(self) -> None:
        # D1: the key carries NO horizon values at all -- structural assertion
        # since "two keys differing only in horizon" is not constructible at
        # this type's own boundary (the behavioural half is T3's).
        field_names = {f.name for f in dataclasses.fields(ForcingTrackKey)}
        assert field_names == {
            "nwp_source",
            "ensemble_mode",
            "time_step",
            "spatial_representation",
            "features",
        }
        assert not any("horizon" in name for name in field_names)

    def test_differing_feature_set_not_equal(self) -> None:
        a = _key(features=frozenset({FeatureName("tp")}))
        b = _key(features=frozenset({FeatureName("t_2m")}))
        assert a != b

    def test_differing_ensemble_mode_not_equal(self) -> None:
        a = _key(ensemble_mode=EnsembleMode.SINGLE)
        b = _key(ensemble_mode=EnsembleMode.ENSEMBLE)
        assert a != b

    def test_differing_time_step_not_equal(self) -> None:
        a = _key(time_step=timedelta(hours=6))
        b = _key(time_step=timedelta(hours=24))
        assert a != b

    def test_differing_spatial_representation_not_equal(self) -> None:
        a = _key(spatial_representation=SpatialRepresentation.BASIN_AVERAGE)
        b = _key(spatial_representation=SpatialRepresentation.POINT)
        assert a != b


class TestTrackProjectionUnion:
    def test_isinstance_narrows(self) -> None:
        from uuid import UUID

        from sapphire_flow.types.ids import ModelId, StationId

        assignment = (StationId(UUID(int=1)), ModelId("m"))
        required: TrackProjection = ForcingRequired(
            key=_key(),
            assignment_horizons={FeatureName("tp"): FutureSteps(value=5)},
            assignment=assignment,  # type: ignore[arg-type]
        )
        none_required: TrackProjection = NoForcingRequired(assignment=assignment)  # type: ignore[arg-type]

        assert isinstance(required, ForcingRequired)
        assert not isinstance(required, NoForcingRequired)
        assert isinstance(none_required, NoForcingRequired)
        assert not isinstance(none_required, ForcingRequired)

        match required:
            case ForcingRequired(key=k):
                assert k == _key()
            case NoForcingRequired():
                pytest.fail("matched wrong arm")


class TestStationTrackOutcomeUnion:
    def test_isinstance_narrows(self) -> None:
        from datetime import UTC, datetime

        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import NwpCycleSource

        available = StationTrackAvailable(
            cycle=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            records=[],
            provenance=NwpCycleSource.PRIMARY,
        )
        unavailable = StationTrackUnavailable(
            reason=StationUnavailableReason.INCOMPLETE_AT_CYCLE
        )
        assert isinstance(available, StationTrackAvailable)
        assert not isinstance(available, StationTrackUnavailable)
        assert isinstance(unavailable, StationTrackUnavailable)
        assert not isinstance(unavailable, StationTrackAvailable)


class TestAdditiveEnumMembers:
    def test_station_unavailable_reason_has_two_new_members(self) -> None:
        assert StationUnavailableReason.INCOMPLETE_AT_CYCLE is not None
        assert StationUnavailableReason.MISSING_POLYGON_COLUMN is not None
        assert (
            StationUnavailableReason.INCOMPLETE_AT_CYCLE
            != StationUnavailableReason.MISSING_POLYGON_COLUMN
        )

    def test_assignment_failure_cause_has_two_new_members(self) -> None:
        assert AssignmentFailureCause.MISSING_CONTEXT is not None
        assert AssignmentFailureCause.TRACK_UNAVAILABLE is not None
        assert (
            AssignmentFailureCause.MISSING_CONTEXT
            != AssignmentFailureCause.TRACK_UNAVAILABLE
        )


class TestRawFetchOutcome:
    def test_fetched_and_absent_are_distinct(self) -> None:
        assert RawFetchStatus.FETCHED != RawFetchStatus.ABSENT_AT_CYCLE

    def test_constructs_with_defaults(self) -> None:
        from datetime import UTC, datetime

        from sapphire_flow.types.datetime import ensure_utc

        outcome = RawFetchOutcome(
            status=RawFetchStatus.ABSENT_AT_CYCLE,
            cycle=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
            stations={},
        )
        assert outcome.missing_polygon_column == frozenset()
        assert outcome.absent_detail is None


def test_resolved_track_request_and_candidate_fetch_result_construct() -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.ids import ModelId, StationId

    assignment = (StationId(UUID(int=1)), ModelId("m"))
    req = ForcingRequired(
        key=_key(),
        assignment_horizons={FeatureName("tp"): FutureSteps(value=5)},
        assignment=assignment,  # type: ignore[arg-type]
    )
    resolved = ResolvedTrackRequest(
        key=_key(),
        fetch_horizons={FeatureName("tp"): FutureSteps(value=5)},
        assignments=(req,),
    )
    assert resolved.assignments == (req,)

    cfr = CandidateFetchResult(
        status=CandidateFetchStatus.COMPLETE,
        cycle=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        raw=None,
    )
    assert cfr.status is CandidateFetchStatus.COMPLETE

    tfr = TrackFetchResult(
        resolved_cycle=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        station_outcomes={},
    )
    assert tfr.station_outcomes == {}
