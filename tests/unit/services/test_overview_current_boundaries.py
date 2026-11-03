# ruff: noqa: F811 -- shared scenario fixture
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from sapphire_flow.exceptions import DailyAuthorityIntegrityError
from sapphire_flow.services.evaluation_forecasts import (
    daily_candidate_from_record,
    read_evaluation_forecasts,
    select_current_daily,
)
from sapphire_flow.services.swiss_overview import (
    CurrentHeadline,
    read_current_headlines,
)
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
    DailyOutputSource,
)
from sapphire_flow.types.daily_forecast_recovery import DailyRecoveryStatus
from sapphire_flow.types.daily_forecast_snapshot import DailyDaySnapshot
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.evaluation_forecast import EvaluationForecasts
from sapphire_flow.types.ids import StationGroupId
from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast
from tests.unit.services.test_evaluation_forecasts import case as case


def batches(case: SimpleNamespace) -> None:
    case.records.fetch_forecasts_by_ids = lambda ids: {
        r.id: r for r in case.records.records if r.id in ids
    }
    case.stores.rejected.fetch_rejected_forecasts_by_ids = lambda ids: {
        r.id: r for r in case.stores.rejected.records if r.id in ids
    }


def panel(case: SimpleNamespace) -> EvaluationForecasts:
    return read_evaluation_forecasts(
        station=case.station,
        settings=case.settings,
        stores=case.stores,
        assignments=(),
        as_of=case.issue + timedelta(hours=1),
    )


def overview(case: SimpleNamespace) -> CurrentHeadline:
    return read_current_headlines(
        station_ids=[case.station.id],
        settings=case.settings,
        stores=case.stores,
        as_of=case.issue + timedelta(hours=1),
    )[case.station.id]


class TestCurrentBoundaries:
    @pytest.mark.parametrize(
        "variant",
        [
            "suspect",
            "incomplete_produced",
            "rejected_allowed",
            "rejected_denied",
            "rejected_wrong_attempt",
        ],
    )
    def test_pure_selector_and_batched_panel_parity(
        self, case: SimpleNamespace, variant: str
    ) -> None:
        summary = case.summary
        ref = summary.outcomes[0].references[0]
        record = case.record
        expected = variant not in {"rejected_denied", "rejected_wrong_attempt"}
        if variant == "suspect":
            record = replace(record, qc_status=QcStatus.QC_SUSPECT)
        elif variant == "incomplete_produced":
            other = replace(summary.claim.frozen.members[0], station_id=UUID(int=123))
            failed = replace(
                other,
                state=DailyMemberState.MODEL_FAILED,
                reason=DailyMemberReason.MODEL_FAILURE,
            )
            summary = replace(
                summary,
                claim=replace(
                    summary.claim,
                    frozen=replace(
                        summary.claim.frozen,
                        members=(*summary.claim.frozen.members, other),
                    ),
                ),
                state=DailyAttemptState.INCOMPLETE,
                outcomes=(*summary.outcomes, failed),
            )
        elif variant.startswith("rejected"):
            ref = replace(ref, source=DailyOutputSource.REJECTED)
            summary = replace(
                summary, outcomes=(replace(summary.outcomes[0], references=(ref,)),)
            )
            record = PersistedRejectedForecast(
                id=ref.output_id,
                attempt_id=UUID(int=99)
                if variant == "rejected_wrong_attempt"
                else summary.claim.id,
                recorded_at=case.issue,
                station_id=case.station.id,
                model_id=case.record.model_id,
                model_artifact_id=summary.claim.frozen.artifact_id,
                group_id=summary.claim.key.group_id,
                issued_at=case.issue,
                parameter="discharge",
                representation=case.record.representation,
                units=case.record.ensemble.units,
                time_step_seconds=3600,
                qc_status=QcStatus.QC_FAILED,
                qc_flags=(
                    QcFlag(
                        rule_id="negative_value",
                        rule_version="1",
                        status=QcStatus.QC_FAILED,
                        detail="negative",
                    ),
                ),
                values={
                    str(k): tuple(
                        (t, -1.0)
                        for t in case.record.ensemble.values["valid_time"]
                        .unique()
                        .sort()
                        .to_list()
                    )
                    for k in (0, 1)
                },
            )
            case.stores.rejected.records = [record]
            if variant != "rejected_denied":
                case.settings = replace(
                    case.settings, plausibility_models=frozenset({record.model_id})
                )
        if not variant.startswith("rejected"):
            case.records.records = [record]
        case.days.day = replace(case.days.day, latest=summary, authoritative=summary)
        batches(case)
        now = case.issue + timedelta(hours=1)
        candidate = daily_candidate_from_record(
            summary=summary, ref=ref, record=record, now=now, settings=case.settings
        )
        pure = select_current_daily(
            station_id=case.station.id,
            parameters=case.settings.parameters,
            days={summary.claim.key: case.days.day},
            candidates={(summary.claim, ref): candidate},
            now=now,
            valid_at=now,
        )
        selected = panel(case).parameters[0].candidate
        headline = overview(case).candidate
        expected_id = ref.output_id if expected else None
        assert (selected.reference.output_id if selected else None) == expected_id
        assert (headline.reference.output_id if headline else None) == expected_id
        assert (
            pure.candidates["discharge"].reference.output_id
            if pure.candidates
            else None
        ) == expected_id

    @pytest.mark.parametrize(
        "fault", ["failed_first", "corrupt_later", "unfinalized_later"]
    )
    def test_multiple_groups_never_select_through_failure(
        self, case: SimpleNamespace, fault: str
    ) -> None:
        first = case.summary
        second_id = StationGroupId(UUID(int=20))
        second = replace(
            first,
            claim=replace(
                first.claim, key=replace(first.claim.key, group_id=second_id)
            ),
        )
        if fault == "failed_first":
            first = replace(
                first,
                state=DailyAttemptState.INCOMPLETE,
                outcomes=(
                    replace(
                        first.outcomes[0],
                        state=DailyMemberState.MODEL_FAILED,
                        reason=DailyMemberReason.MODEL_FAILURE,
                        references=(),
                    ),
                ),
            )
        elif fault == "corrupt_later":
            second = replace(
                second,
                claim=replace(
                    second.claim, key=replace(second.claim.key, deployment="wrong")
                ),
            )
        else:
            second = replace(
                second, state=DailyAttemptState.ACTIVE, finished_at=None, outcomes=None
            )
        empty = DailyDaySnapshot(
            latest=None,
            authoritative=None,
            recovery_status=DailyRecoveryStatus.UNTOUCHED,
        )
        days = {
            first.claim.key: replace(case.days.day, latest=first, authoritative=first),
            replace(second.claim.key, deployment=case.settings.deployment): replace(
                case.days.day, latest=second, authoritative=second
            ),
        }
        case.settings = replace(
            case.settings, group_ids=(second_id, first.claim.key.group_id)
        )
        case.stores = replace(
            case.stores,
            daily=SimpleNamespace(
                read_day=lambda key: next(
                    (
                        day
                        for stored_key, day in days.items()
                        if stored_key.group_id == key.group_id
                        and stored_key.context.issue_time == key.context.issue_time
                    ),
                    empty,
                )
            ),
            groups=SimpleNamespace(
                fetch_group=lambda gid: SimpleNamespace(
                    id=gid, tenant_id=case.station.tenant_id
                )
            ),
        )
        batches(case)
        if fault == "failed_first":
            assert panel(case).parameters[0].candidate is None
            assert overview(case).candidate is None
        else:
            with pytest.raises(DailyAuthorityIntegrityError, match="scope|finalized"):
                panel(case)
            with pytest.raises(DailyAuthorityIntegrityError, match="scope|finalized"):
                overview(case)
        now = case.issue + timedelta(hours=1)
        kwargs = dict(
            station_id=case.station.id,
            parameters=case.settings.parameters,
            days=days,
            candidates={},
            now=now,
            valid_at=now,
        )
        if fault == "failed_first":
            assert select_current_daily(**kwargs).candidates == {}
        else:
            with pytest.raises(DailyAuthorityIntegrityError, match="scope|finalized"):
                select_current_daily(**kwargs)

    def test_yesterday_diagnostic_corruption_does_not_poison_current(
        self, case: SimpleNamespace
    ) -> None:
        today = case.days.day
        case.stores = replace(
            case.stores, daily=SimpleNamespace(read_day=lambda key: today)
        )
        batches(case)
        with pytest.raises(DailyAuthorityIntegrityError, match="scope"):
            panel(case)
        assert overview(case).candidate.reference.output_id == case.record.id


class TestAuthorityIdentityCollisions:
    @pytest.mark.parametrize(
        "source", [DailyOutputSource.FORECAST, DailyOutputSource.REJECTED]
    )
    @pytest.mark.parametrize("first_valid", [True, False])
    def test_same_reference_keeps_first_group_contract_and_claim(
        self, case: SimpleNamespace, source: DailyOutputSource, first_valid: bool
    ) -> None:
        first = case.summary
        ref = replace(first.outcomes[0].references[0], source=source)
        first = replace(
            first, outcomes=(replace(first.outcomes[0], references=(ref,)),)
        )
        second_id = StationGroupId(UUID(int=20))
        second = replace(
            first,
            claim=replace(
                first.claim,
                id=UUID(int=30),
                key=replace(first.claim.key, group_id=second_id),
            ),
        )
        invalid = first if not first_valid else second
        if source is DailyOutputSource.FORECAST:
            invalid = replace(
                invalid,
                claim=replace(
                    invalid.claim,
                    frozen=replace(
                        invalid.claim.frozen,
                        output_contracts=(
                            replace(
                                invalid.claim.frozen.output_contracts[0], units="ft3/s"
                            ),
                        ),
                    ),
                ),
            )
            if first_valid:
                second = invalid
            else:
                first = invalid
        else:
            authority = first if first_valid else second
            record = PersistedRejectedForecast(
                id=ref.output_id,
                attempt_id=authority.claim.id,
                recorded_at=case.issue,
                station_id=case.station.id,
                model_id=case.record.model_id,
                model_artifact_id=authority.claim.frozen.artifact_id,
                group_id=authority.claim.key.group_id,
                issued_at=case.issue,
                parameter="discharge",
                representation=case.record.representation,
                units=case.record.ensemble.units,
                time_step_seconds=3600,
                qc_status=QcStatus.QC_FAILED,
                qc_flags=(
                    QcFlag(
                        rule_id="negative_value",
                        rule_version="1",
                        status=QcStatus.QC_FAILED,
                        detail="negative",
                    ),
                ),
                values={
                    str(k): tuple(
                        (t, -1.0)
                        for t in case.record.ensemble.values["valid_time"]
                        .unique()
                        .sort()
                        .to_list()
                    )
                    for k in (0, 1)
                },
            )
            case.stores.rejected.records = [record]
            case.settings = replace(
                case.settings, plausibility_models=frozenset({record.model_id})
            )
        empty = DailyDaySnapshot(
            latest=None,
            authoritative=None,
            recovery_status=DailyRecoveryStatus.UNTOUCHED,
        )
        days = {
            summary.claim.key: replace(
                case.days.day, latest=summary, authoritative=summary
            )
            for summary in (first, second)
        }
        case.settings = replace(
            case.settings, group_ids=(second_id, first.claim.key.group_id)
        )
        case.stores = replace(
            case.stores,
            daily=SimpleNamespace(
                read_day=lambda key: next(
                    (
                        day
                        for stored, day in days.items()
                        if stored.group_id == key.group_id
                        and stored.context.issue_time == key.context.issue_time
                    ),
                    empty,
                )
            ),
            groups=SimpleNamespace(
                fetch_group=lambda gid: SimpleNamespace(
                    id=gid, tenant_id=case.station.tenant_id
                )
            ),
        )
        batches(case)
        expected = ref.output_id if first_valid else None
        for candidate in (
            panel(case).parameters[0].candidate,
            overview(case).candidate,
        ):
            assert (candidate.reference.output_id if candidate else None) == expected
