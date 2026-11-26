# ruff: noqa: F811 -- Shared selector fixture.
"""Issue #581: retained earlier same-day result after a failed later attempt."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.api.routes.api_swiss_overview import _cmal
from sapphire_flow.exceptions import DailyAuthorityIntegrityError
from sapphire_flow.services.evaluation_forecasts import (
    daily_day_available,
    select_current_daily_retained,
    select_retained_member,
)
from sapphire_flow.services.swiss_overview import (
    CurrentHeadline,
    read_current_headlines,
)
from sapphire_flow.types.daily_forecast import (
    DailyAttemptOrdinal,
    DailyAttemptState,
    ScheduledDailyForecast,
)
from sapphire_flow.types.daily_forecast_claim import DailyFailureReason
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberReason,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_forecast_recovery import DailyRecoveryStatus
from sapphire_flow.types.daily_forecast_snapshot import (
    DailyAttemptSnapshot,
    DailyDaySnapshot,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastStatus
from sapphire_flow.types.evaluation_forecast import EvaluationCandidate
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.swiss_overview import HeadlineAbsence
from tests.unit.services.test_evaluation_forecasts import case  # noqa: F401


def _failed(
    snapshot: DailyAttemptSnapshot,
    n: int,
    state: DailyMemberState = DailyMemberState.MODEL_FAILED,
    *,
    references: tuple[DailyOutputReference, ...] = (),
) -> DailyAttemptSnapshot:
    member = snapshot.outcomes[0]  # type: ignore[index]
    reason = {
        DailyMemberState.MODEL_FAILED: DailyMemberReason.MODEL_FAILURE,
        DailyMemberState.INTEGRITY_REJECTED: DailyMemberReason.OUTPUT_INTEGRITY,
    }[state]
    claim = replace(
        snapshot.claim,
        id=UUID(int=100 + n),
        ordinal=DailyAttemptOrdinal.RETRY,
        parent_id=snapshot.claim.id,
    )
    return replace(
        snapshot,
        claim=claim,
        state=DailyAttemptState.INCOMPLETE,
        outcomes=(
            DailyMemberOutcome(
                station_id=member.station_id,
                required_parameters=member.required_parameters,
                state=state,
                reason=reason,
                references=references,
            ),
        ),
    )


def _produced(
    snapshot: DailyAttemptSnapshot, n: int
) -> tuple[DailyAttemptSnapshot, DailyOutputReference]:
    member = snapshot.outcomes[0]  # type: ignore[index]
    ref = replace(member.references[0], output_id=UUID(int=200 + n))
    claim = replace(snapshot.claim, id=UUID(int=300 + n))
    return (
        replace(snapshot, claim=claim, outcomes=(replace(member, references=(ref,)),)),
        ref,
    )


def _day(
    authoritative: DailyAttemptSnapshot, *earlier: DailyAttemptSnapshot
) -> DailyDaySnapshot:
    return DailyDaySnapshot(
        latest=authoritative,
        authoritative=authoritative,
        recovery_status=DailyRecoveryStatus.UNTOUCHED,
        earlier=earlier,
    )


def _candidate(
    case: SimpleNamespace, ref: DailyOutputReference, shift: int = 0
) -> EvaluationCandidate:
    record = case.record
    if shift:
        record = replace(
            record,
            ensemble=replace(
                record.ensemble,
                values=record.ensemble.values.with_columns(
                    pl.col("valid_time") + timedelta(hours=shift)
                ),
            ),
        )
    return EvaluationCandidate(reference=ref, record=record, age=timedelta(0))


def _select(
    case: SimpleNamespace,
    day: DailyDaySnapshot,
    candidates: dict,
    *,
    hours: float = 1.0,
):
    return select_retained_member(
        day=day,
        station_id=case.station.id,
        parameters=("discharge",),
        candidates=candidates,
        valid_at=ensure_utc(case.issue + timedelta(hours=hours)),
    )


class TestSelector:
    """`earlier` is built directly: no Postgres."""

    def test_failed_retry_keeps_earlier_with_label(self, case: SimpleNamespace) -> None:
        retry = _failed(case.summary, 1)
        ref = case.summary.outcomes[0].references[0]
        candidate = _candidate(case, ref)
        selection, retained = _select(
            case, _day(retry, case.summary), {(case.summary.claim, ref): candidate}
        )
        assert selection.candidates == {"discharge": candidate}
        assert retained is not None
        assert retained.earlier_attempt_id == case.summary.claim.id
        assert retained.earlier_issued_at == case.issue
        assert retained.superseding_attempt_id == retry.claim.id
        assert retained.superseding_state is DailyMemberState.MODEL_FAILED
        assert retained.superseding_reason is DailyMemberReason.MODEL_FAILURE

    def test_candidates_are_keyed_by_the_earlier_claim(
        self, case: SimpleNamespace
    ) -> None:
        retry = _failed(case.summary, 1)
        ref = case.summary.outcomes[0].references[0]
        # A candidate cached under the authoritative claim must not be used.
        selection, retained = _select(
            case, _day(retry, case.summary), {(retry.claim, ref): _candidate(case, ref)}
        )
        assert selection.candidates == {} and retained is None

    def test_member_produced_in_authority_never_walks(
        self, case: SimpleNamespace
    ) -> None:
        newer, newer_ref = _produced(case.summary, 1)
        ref = case.summary.outcomes[0].references[0]
        selection, retained = _select(
            case,
            _day(newer, case.summary),
            {
                (newer.claim, newer_ref): _candidate(case, newer_ref),
                (case.summary.claim, ref): _candidate(case, ref),
            },
        )
        assert selection.candidates["discharge"].reference == newer_ref
        assert retained is None

    def test_produced_but_expired_authority_stays_expired(
        self, case: SimpleNamespace
    ) -> None:
        newer, newer_ref = _produced(case.summary, 1)
        ref = case.summary.outcomes[0].references[0]
        selection, retained = _select(
            case,
            _day(newer, case.summary),
            {
                (newer.claim, newer_ref): _candidate(case, newer_ref),
                # Shifted forward, the earlier result would cover valid_at.
                (case.summary.claim, ref): _candidate(case, ref, shift=4),
            },
            hours=5,
        )
        assert selection.candidates == {}
        assert selection.uncovered == {"discharge"}
        assert retained is None

    def test_expired_earlier_is_uncovered_not_retained(
        self, case: SimpleNamespace
    ) -> None:
        retry = _failed(case.summary, 1)
        ref = case.summary.outcomes[0].references[0]
        selection, retained = _select(
            case,
            _day(retry, case.summary),
            {(case.summary.claim, ref): _candidate(case, ref)},
            hours=5,
        )
        assert selection.candidates == {}
        assert selection.uncovered == {"discharge"}
        assert retained is None

    def test_rejected_earlier_member_never_falls_through(
        self, case: SimpleNamespace
    ) -> None:
        retry = _failed(case.summary, 1)
        newer_earlier, newer_ref = _produced(case.summary, 1)
        older_ref = case.summary.outcomes[0].references[0]
        selection, retained = _select(
            case,
            _day(retry, newer_earlier, case.summary),
            {
                # The builder refused the newer record (superseded, QC reject).
                (newer_earlier.claim, newer_ref): None,
                (case.summary.claim, older_ref): _candidate(case, older_ref),
            },
        )
        assert selection.candidates == {} and retained is None

    def test_integrity_rejected_earlier_member_is_not_a_result(
        self, case: SimpleNamespace
    ) -> None:
        retry = _failed(case.summary, 1)
        member = case.summary.outcomes[0]
        rejected_ref = replace(
            member.references[0],
            source=DailyOutputSource.REJECTED,
            output_id=UUID(int=400),
        )
        rejected = _failed(
            case.summary,
            2,
            DailyMemberState.INTEGRITY_REJECTED,
            references=(rejected_ref,),
        )
        older, older_ref = _produced(case.summary, 3)
        candidates = {
            (rejected.claim, rejected_ref): _candidate(case, rejected_ref),
            (older.claim, older_ref): _candidate(case, older_ref),
        }
        # Only the integrity-rejected attempt: nothing to retain.
        selection, retained = _select(case, _day(retry, rejected), candidates)
        assert selection.candidates == {} and retained is None
        # The walk skips it and stops at the first PRODUCED member.
        selection, retained = _select(case, _day(retry, rejected, older), candidates)
        assert selection.candidates["discharge"].reference == older_ref
        assert retained is not None
        assert retained.earlier_attempt_id == older.claim.id

    def test_integrity_rejected_authoritative_member_is_not_a_result(
        self, case: SimpleNamespace
    ) -> None:
        rejected_ref = replace(
            case.summary.outcomes[0].references[0],
            source=DailyOutputSource.REJECTED,
            output_id=UUID(int=402),
        )
        newest = _failed(
            case.summary,
            1,
            DailyMemberState.INTEGRITY_REJECTED,
            references=(rejected_ref,),
        )
        selection, retained = _select(
            case,
            _day(newest),
            {(newest.claim, rejected_ref): _candidate(case, rejected_ref)},
        )
        assert selection.candidates == {} and retained is None

    def test_newest_integrity_rejected_member_shows_earlier_with_state(
        self, case: SimpleNamespace
    ) -> None:
        member = case.summary.outcomes[0]
        rejected_ref = replace(
            member.references[0],
            source=DailyOutputSource.REJECTED,
            output_id=UUID(int=401),
        )
        newest = _failed(
            case.summary,
            1,
            DailyMemberState.INTEGRITY_REJECTED,
            references=(rejected_ref,),
        )
        ref = member.references[0]
        _, retained = _select(
            case,
            _day(newest, case.summary),
            {(case.summary.claim, ref): _candidate(case, ref)},
        )
        assert retained is not None
        assert retained.superseding_state is DailyMemberState.INTEGRITY_REJECTED
        assert retained.superseding_reason is DailyMemberReason.OUTPUT_INTEGRITY

    def test_absent_station_without_earlier_result_returns_none(
        self, case: SimpleNamespace
    ) -> None:
        retry = _failed(case.summary, 1)
        result = select_retained_member(
            day=_day(retry),
            station_id=StationId(UUID(int=999)),
            parameters=("discharge",),
            candidates={},
            valid_at=case.issue,
        )
        assert result is None
        # A present, non-produced member keeps the existing empty selection.
        selection, retained = _select(case, _day(retry), {})
        assert selection.candidates == {} and retained is None

    def test_absent_station_uses_earlier_with_empty_member_label(
        self, case: SimpleNamespace
    ) -> None:
        member = case.summary.outcomes[0]
        other = StationId(UUID(int=999))
        newest = replace(
            case.summary,
            claim=replace(
                case.summary.claim,
                id=UUID(int=500),
                frozen=replace(
                    case.summary.claim.frozen,
                    members=(
                        replace(
                            member,
                            station_id=other,
                            state=DailyMemberState.PENDING,
                            references=(),
                        ),
                    ),
                ),
            ),
            state=DailyAttemptState.INCOMPLETE,
            outcomes=(
                DailyMemberOutcome(
                    station_id=other,
                    required_parameters=member.required_parameters,
                    state=DailyMemberState.MODEL_FAILED,
                    reason=DailyMemberReason.MODEL_FAILURE,
                ),
            ),
        )
        ref = member.references[0]
        result = _select(
            case,
            _day(newest, case.summary),
            {(case.summary.claim, ref): _candidate(case, ref)},
        )
        assert result is not None and result[1] is not None
        assert result[1].superseding_state is None
        assert result[1].superseding_reason is None

    def test_no_days_selects_nothing(self, case: SimpleNamespace) -> None:
        selection, retained = select_current_daily_retained(
            station_id=case.station.id,
            parameters=("discharge",),
            days={},
            candidates={},
            now=case.issue,
            valid_at=case.issue,
        )
        assert selection.candidates == {} and retained is None


class TestScope:
    def test_earlier_outside_the_issue_day_is_refused(
        self, case: SimpleNamespace
    ) -> None:
        key = case.summary.claim.key
        yesterday = ensure_utc(case.issue - timedelta(days=1))
        old = replace(
            case.summary,
            claim=replace(
                case.summary.claim,
                key=replace(
                    key, context=ScheduledDailyForecast(scheduled_tick=yesterday)
                ),
            ),
            state=DailyAttemptState.FAILED,
            outcomes=None,
            failure_reason=DailyFailureReason.MODEL_FAILURE,
        )
        with pytest.raises(DailyAuthorityIntegrityError):
            daily_day_available(_day(_failed(case.summary, 1), old), key, case.issue)

    def test_earlier_wrong_group_is_refused(self, case: SimpleNamespace) -> None:
        key = case.summary.claim.key
        other = replace(
            case.summary,
            claim=replace(
                case.summary.claim,
                key=replace(key, group_id=StationGroupId(UUID(int=77))),
            ),
        )
        with pytest.raises(DailyAuthorityIntegrityError):
            daily_day_available(_day(_failed(case.summary, 1), other), key, case.issue)

    def test_earlier_from_the_future_makes_the_day_unavailable(
        self, case: SimpleNamespace
    ) -> None:
        later = replace(
            case.summary, finished_at=ensure_utc(case.issue + timedelta(hours=3))
        )
        day = _day(_failed(case.summary, 1), later)
        assert not daily_day_available(
            day, case.summary.claim.key, ensure_utc(case.issue + timedelta(hours=1))
        )


def _wire(case: SimpleNamespace, day: DailyDaySnapshot) -> SimpleNamespace:
    case.days.day = day
    calls = SimpleNamespace(accepted=[], rejected=[])

    def accepted(ids: list) -> dict:
        calls.accepted.append(list(ids))
        return {r.id: r for r in case.records.records if r.id in ids}

    def rejected(ids: list) -> dict:
        calls.rejected.append(list(ids))
        return {}

    case.records.fetch_forecasts_by_ids = accepted
    case.stores.rejected.fetch_rejected_forecasts_by_ids = rejected
    return calls


def _headline(case: SimpleNamespace, hours: float = 1.0) -> CurrentHeadline:
    return read_current_headlines(
        station_ids=[case.station.id],
        settings=case.settings,
        stores=case.stores,
        as_of=ensure_utc(case.issue + timedelta(hours=hours)),
    )[case.station.id]


class TestHeadlines:
    def test_failed_retry_returns_earlier_with_label(
        self, case: SimpleNamespace
    ) -> None:
        retry = _failed(case.summary, 1)
        _wire(case, _day(retry, case.summary))
        headline = _headline(case)
        assert headline.candidate is not None and headline.absence is None
        assert headline.retained is not None
        assert headline.retained.superseding_attempt_id == retry.claim.id

    def test_no_failed_retry_fetches_exactly_the_same_ids(
        self, case: SimpleNamespace
    ) -> None:
        ref = case.summary.outcomes[0].references[0]
        older, _ = _produced(case.summary, 1)
        # With or without an earlier attempt, a produced member needs no fallback.
        for day in (_day(case.summary), _day(case.summary, older)):
            calls = _wire(case, day)
            headline = _headline(case)
            assert headline.retained is None and headline.candidate is not None
            assert calls.accepted == [[ref.output_id]]
            assert calls.rejected == [[]]

    def test_failed_retry_fetches_the_earlier_references(
        self, case: SimpleNamespace
    ) -> None:
        ref = case.summary.outcomes[0].references[0]
        calls = _wire(case, _day(_failed(case.summary, 1), case.summary))
        _headline(case)
        assert calls.accepted == [[ref.output_id]]

    def test_superseded_earlier_record_is_unavailable(
        self, case: SimpleNamespace
    ) -> None:
        case.records.records = [replace(case.record, status=ForecastStatus.SUPERSEDED)]
        _wire(case, _day(_failed(case.summary, 1), case.summary))
        headline = _headline(case)
        assert headline.candidate is None and headline.retained is None
        assert headline.absence is HeadlineAbsence.NO_CURRENT_CANDIDATE

    def test_expired_earlier_is_expired_horizon(self, case: SimpleNamespace) -> None:
        _wire(case, _day(_failed(case.summary, 1), case.summary))
        headline = _headline(case, hours=5)
        assert headline.candidate is None and headline.retained is None
        assert headline.absence is HeadlineAbsence.EXPIRED_HORIZON


class TestCmalLabel:
    def _out(self, case: SimpleNamespace, *, withheld: bool) -> dict:
        _wire(case, _day(_failed(case.summary, 1), case.summary))
        return _cmal(
            _headline(case),
            as_of=ensure_utc(case.issue + timedelta(hours=1)),
            withheld=withheld,
        ).model_dump(mode="json")

    def test_available_variant_carries_the_label(self, case: SimpleNamespace) -> None:
        out = self._out(case, withheld=False)
        assert out["state"] == "available"
        assert out["retained_result"]["kind"] == "earlier_attempt_after_failed_retry"
        assert out["retained_result"]["superseding_state"] == "model_failed"

    def test_withheld_variant_carries_the_label_and_no_values(
        self, case: SimpleNamespace
    ) -> None:
        out = self._out(case, withheld=True)
        assert out["state"] == "withheld"
        assert out["output_id"] == str(case.record.id)
        assert out["retained_result"]["original_attempt_id"] == str(
            case.summary.claim.id
        )
        assert "peak_value" not in out and "series" not in out

    def test_no_retained_result_is_null(self, case: SimpleNamespace) -> None:
        _wire(case, _day(case.summary))
        out = _cmal(
            _headline(case),
            as_of=ensure_utc(case.issue + timedelta(hours=1)),
            withheld=False,
        ).model_dump(mode="json")
        assert out["retained_result"] is None
