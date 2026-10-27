from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.services.evaluation_forecasts import (
    EvaluationStores,
    read_evaluation_forecasts,
)
from sapphire_flow.types.daily_forecast import (
    DailyAttemptOrdinal,
    DailyAttemptState,
    ScheduledDailyForecast,
)
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    FrozenDailyAssignment,
)
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
from sapphire_flow.types.daily_output import DailyOutputContract
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import (
    ForecastDataUse,
    ForecastStatus,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.evaluation_forecast import (
    EvaluationAssignment,
    EvaluationForecasts,
    EvaluationReadSettings,
    EvaluationSelectionKind,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationGroupId
from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast
from tests.conftest import make_forecast_ensemble, make_station_config


class Records:
    data_use = ForecastDataUse.STANDARD

    def __init__(self, records: list) -> None:
        self.records = records
        self.reads = []

    def fetch_forecast(self, output_id: UUID) -> OperationalForecast | None:
        self.reads.append(output_id)
        return next((r for r in self.records if r.id == output_id), None)

    fetch_rejected_forecast = fetch_forecast

    def fetch_latest_forecast_at(
        self, station_id: object, model_id: ModelId, as_of: object
    ) -> OperationalForecast | None:
        return max(
            (
                r
                for r in self.records
                if r.station_id == station_id
                and r.model_id == model_id
                and r.issued_at <= as_of
                and r.status is not ForecastStatus.SUPERSEDED
                and r.combination_strategy is None
                and r.data_use is self.data_use
            ),
            key=lambda r: (r.issued_at, r.version, str(r.id)),
            default=None,
        )

    def fetch_latest_rejected_forecast_at(
        self,
        station_id: object,
        model_id: ModelId,
        as_of: object,
        group_ids: tuple[StationGroupId, ...],
    ) -> PersistedRejectedForecast | None:
        return max(
            (
                r
                for r in self.records
                if r.station_id == station_id
                and r.model_id == model_id
                and r.issued_at <= as_of
                and r.recorded_at <= as_of
                and r.data_use is self.data_use
                and (r.group_id is None or r.group_id in group_ids)
            ),
            key=lambda r: (r.issued_at, r.recorded_at, str(r.id)),
            default=None,
        )

    def fetch_forecasts_in_range(self, *args: object, **kwargs: object) -> list:
        return self.records


class Days:
    def __init__(self, day: DailyDaySnapshot) -> None:
        self.day = day

    def read_day(self, key: DailyClaimKey) -> DailyDaySnapshot:
        if (
            self.day.latest
            and key.context.issue_time == self.day.latest.claim.key.context.issue_time
        ):
            return self.day
        return DailyDaySnapshot(
            latest=None,
            authoritative=None,
            recovery_status=DailyRecoveryStatus.UNTOUCHED,
        )


@pytest.fixture
def case() -> SimpleNamespace:
    station = make_station_config()
    model = ModelId("cmal_small")
    ensemble = make_forecast_ensemble(
        station_id=station.id, model_id=model, n_steps=2, n_members=2
    )
    issue = ensemble.issued_at
    group = StationGroupId(UUID(int=4))
    settings = EvaluationReadSettings(
        tenant_id=station.tenant_id,
        deployment="bafu",
        group_ids=(group,),
        daily_model_id=model,
        parameters=("discharge",),
    )
    contract = DailyOutputContract(
        parameter="discharge",
        units=ensemble.units,
        representation=ensemble.representation,
        time_step=ensemble.time_step,
        first_lead=timedelta(hours=1),
        horizon_steps=2,
        series_keys=(0.0, 1.0),
    )
    pending = DailyMemberOutcome(
        station_id=station.id,
        required_parameters=("discharge",),
        state=DailyMemberState.PENDING,
    )
    claim = DailyClaim(
        id=UUID(int=5),
        owner_id=UUID(int=6),
        key=DailyClaimKey(
            tenant_id=station.tenant_id,
            deployment="bafu",
            group_id=group,
            model_id=model,
            context=ScheduledDailyForecast(scheduled_tick=issue),
        ),
        ordinal=DailyAttemptOrdinal.INITIAL,
        frozen=FrozenDailyAssignment(
            artifact_id=ArtifactId(UUID(int=7)),
            configuration_sha256="a" * 64,
            members=(pending,),
            output_contracts=(contract,),
        ),
        parent_id=None,
        started_at=issue,
    )
    ref = DailyOutputReference(
        key=ForecastOutputKey(
            station_id=station.id,
            model_id=model,
            issued_at=issue,
            parameter="discharge",
        ),
        source=DailyOutputSource.FORECAST,
        output_id=UUID(int=8),
    )
    member = replace(pending, state=DailyMemberState.PRODUCED, references=(ref,))
    summary = DailyAttemptSnapshot(
        claim=claim,
        state=DailyAttemptState.COMPLETE,
        finished_at=issue,
        outcomes=(member,),
        failure_reason=None,
        recovery=None,
    )
    record = OperationalForecast(
        id=ForecastId(ref.output_id),
        station_id=station.id,
        model_id=model,
        model_artifact_id=claim.frozen.artifact_id,
        issued_at=issue,
        nwp_cycle_reference_time=None,
        nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
        representation=ensemble.representation,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=issue,
        updated_at=issue,
        qc_status=QcStatus.QC_PASSED,
    )
    days = Days(
        DailyDaySnapshot(
            latest=summary,
            authoritative=summary,
            recovery_status=DailyRecoveryStatus.UNTOUCHED,
        )
    )
    records = Records([record])
    stores = EvaluationStores(
        daily=days,
        forecasts=records,
        rejected=Records([]),
        groups=SimpleNamespace(
            fetch_group=lambda _: SimpleNamespace(id=group, tenant_id=station.tenant_id)
        ),
    )
    return SimpleNamespace(
        station=station,
        settings=settings,
        stores=stores,
        days=days,
        records=records,
        record=record,
        summary=summary,
        contract=contract,
        issue=issue,
    )


def read(case: SimpleNamespace, **kwargs: object) -> EvaluationForecasts:
    args = dict(
        station=case.station,
        settings=case.settings,
        assignments=(),
        stores=case.stores,
        as_of=case.issue + timedelta(hours=1),
    )
    args.update(kwargs)
    return read_evaluation_forecasts(**args)


class TestReadEvaluationForecasts:
    def test_exact_reference_values_and_clock(self, case: SimpleNamespace) -> None:
        result = read(case)
        assert result.parameters[0].candidate.record.ensemble.values.equals(
            case.record.ensemble.values
        )
        assert result.issue_time == case.issue
        assert result.parameters[0].selection is EvaluationSelectionKind.CURRENT

    def test_half_open_coverage_and_future_valid_time(
        self, case: SimpleNamespace
    ) -> None:
        assert (
            read(case, valid_at=case.issue + timedelta(hours=3))
            .parameters[0]
            .absence.value
            == "no_valid_time_coverage"
        )
        assert (
            read(case, valid_at=case.issue + timedelta(days=2)).issue_time == case.issue
        )

    def test_naive_time_refused_before_reads(self, case: SimpleNamespace) -> None:
        with pytest.raises(ValueError, match="Naive"):
            read(case, valid_at=case.issue.replace(tzinfo=None))
        assert case.records.reads == []

    def test_whole_summary_replacement(self, case: SimpleNamespace) -> None:
        failed = replace(
            case.summary.outcomes[0],
            state=DailyMemberState.MODEL_FAILED,
            references=(),
            reason=DailyMemberReason.MODEL_FAILURE,
        )
        retry = replace(
            case.summary,
            claim=replace(
                case.summary.claim, id=UUID(int=9), ordinal=DailyAttemptOrdinal.RETRY
            ),
            state=DailyAttemptState.INCOMPLETE,
            outcomes=(failed,),
        )
        case.days.day = replace(case.days.day, latest=retry, authoritative=retry)
        result = read(
            case,
            assignments=(
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=1,
                    contracts=(case.contract,),
                ),
            ),
        )
        assert result.models[0].records == ()
        assert result.models[0].authoritative_attempt_id == retry.claim.id
        assert result.parameters[0].candidate is None
        assert result.daily[0].authoritative_attempt_id == retry.claim.id
        assert case.records.reads == []

    def test_active_retry_does_not_promote_superseding_row(
        self, case: SimpleNamespace
    ) -> None:
        retry = replace(
            case.summary,
            claim=replace(case.summary.claim, id=UUID(int=9)),
            state=DailyAttemptState.ACTIVE,
            finished_at=None,
            outcomes=None,
        )
        case.days.day = replace(case.days.day, latest=retry)
        case.records.records = [
            replace(case.record, status=ForecastStatus.SUPERSEDED),
            replace(case.record, id=ForecastId(UUID(int=10))),
        ]
        result = read(
            case,
            assignments=(
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=1,
                    contracts=(case.contract,),
                ),
            ),
        )
        assert result.parameters[0].candidate is None
        assert result.daily[0].latest_state is DailyAttemptState.ACTIVE
        assert result.models[0].latest_state is DailyAttemptState.ACTIVE
        assert result.models[0].records == ()
        assert result.models[0].authoritative_attempt_id == case.summary.claim.id
        assert result.daily[0].authoritative_attempt_id == case.summary.claim.id
        assert case.records.reads == [case.record.id]
        diagnostic = result.daily[0].references[0]
        assert diagnostic.reference.output_id == case.record.id
        assert diagnostic.usability.value == "superseded"

    @pytest.mark.parametrize("damage", ["nan", "shape", "raw", "unknown", "artifact"])
    def test_invalid_records_refused(self, case: SimpleNamespace, damage: str) -> None:
        record = case.record
        if damage == "nan":
            record = replace(
                record,
                ensemble=replace(
                    record.ensemble,
                    values=record.ensemble.values.with_columns(
                        pl.lit(float("nan")).alias("value")
                    ),
                ),
            )
        elif damage == "shape":
            record = replace(
                record,
                ensemble=replace(
                    record.ensemble, values=record.ensemble.values.head(1)
                ),
            )
        elif damage == "raw":
            record = replace(record, qc_status=QcStatus.RAW)
        elif damage == "artifact":
            record = replace(record, model_artifact_id=ArtifactId(UUID(int=100)))
        else:
            record = replace(
                record,
                qc_flags=(
                    QcFlag(
                        rule_id="unknown",
                        rule_version="1",
                        status=QcStatus.QC_FAILED,
                        detail=None,
                    ),
                ),
            )
        case.records.records = [record]
        assert read(case).parameters[0].candidate is None

    def test_stale_summary_diagnostic_only(self, case: SimpleNamespace) -> None:
        result = read(
            case,
            as_of=case.issue + timedelta(days=1, hours=1),
            valid_at=case.issue + timedelta(hours=1),
        )
        assert result.parameters[0].candidate is None
        assert result.daily[1].member.state is DailyMemberState.PRODUCED
        assert result.daily[1].references[0].usability.value == "usable"

    def test_current_fallback_and_future_row_shadow(
        self, case: SimpleNamespace
    ) -> None:
        model = ModelId("persistence")
        record = replace(
            case.record,
            model_id=model,
            ensemble=replace(case.record.ensemble, model_id=model),
        )
        future = replace(record, issued_at=ensure_utc(case.issue + timedelta(hours=12)))
        case.records.records = [future, record]
        case.days.day = replace(case.days.day, latest=None, authoritative=None)
        result = read(
            case,
            assignments=(
                EvaluationAssignment(
                    model_id=model, priority=100, contracts=(case.contract,)
                ),
            ),
        )
        assert result.parameters[0].selection is EvaluationSelectionKind.FALLBACK
        assert result.parameters[0].candidate.record == record
        assert result.parameters[0].daily_absence.value == "no_usable_output"
        assert result.parameters[0].absence is None

    def test_tenant_scope_precedes_value_reads(self, case: SimpleNamespace) -> None:
        with pytest.raises(ValueError, match="tenant"):
            read(case, settings=replace(case.settings, tenant_id=UUID(int=99)))
        assert case.records.reads == []

    def test_rejected_plausibility_requires_allowlist(
        self, case: SimpleNamespace
    ) -> None:
        ref = replace(
            case.summary.outcomes[0].references[0], source=DailyOutputSource.REJECTED
        )
        member = replace(case.summary.outcomes[0], references=(ref,))
        summary = replace(case.summary, outcomes=(member,))
        case.days.day = replace(case.days.day, latest=summary, authoritative=summary)
        values = {
            str(k): tuple(
                (t, -1.0)
                for t in case.record.ensemble.values["valid_time"]
                .unique()
                .sort()
                .to_list()
            )
            for k in (0, 1)
        }
        rejected = PersistedRejectedForecast(
            id=ref.output_id,
            attempt_id=summary.claim.id,
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
            values=values,
        )
        case.stores.rejected.records = [rejected]
        assert read(case).parameters[0].candidate is None
        settings = replace(
            case.settings, plausibility_models=frozenset({case.record.model_id})
        )
        assert read(case, settings=settings).parameters[0].candidate.record == rejected
        case.stores.rejected.records = [replace(rejected, attempt_id=UUID(int=99))]
        assert read(case, settings=settings).parameters[0].candidate is None

    def test_additional_required_parameter_is_not_hidden(
        self, case: SimpleNamespace
    ) -> None:
        pending = replace(
            case.summary.claim.frozen.members[0],
            required_parameters=("discharge", "temperature"),
        )
        frozen = replace(case.summary.claim.frozen, members=(pending,))
        failed = replace(
            pending,
            state=DailyMemberState.MODEL_FAILED,
            reason=DailyMemberReason.MODEL_FAILURE,
        )
        summary = replace(
            case.summary,
            claim=replace(case.summary.claim, frozen=frozen),
            state=DailyAttemptState.INCOMPLETE,
            outcomes=(failed,),
        )
        case.days.day = replace(case.days.day, latest=summary, authoritative=summary)
        assert [p.parameter for p in read(case).parameters] == [
            "discharge",
            "temperature",
        ]

    def test_wrong_purpose_store_refused_before_read(
        self, case: SimpleNamespace
    ) -> None:
        from sapphire_flow.services.forecast_read import ForecastReadUnavailableError

        case.stores.rejected.data_use = ForecastDataUse.EXPIRED_RATING_TEST
        with pytest.raises(ForecastReadUnavailableError, match="unavailable"):
            read(case)
        assert case.records.reads == []

    def test_mixed_purpose_fallback_batch_refused(self, case: SimpleNamespace) -> None:
        from sapphire_flow.services.forecast_read import ForecastReadUnavailableError

        case.days.day = replace(case.days.day, latest=None, authoritative=None)
        case.records.records = [
            case.record,
            SimpleNamespace(data_use=ForecastDataUse.EXPIRED_RATING_TEST),
        ]
        assignments = (
            EvaluationAssignment(
                model_id=ModelId("persistence"),
                priority=100,
                contracts=(case.contract,),
            ),
        )
        with pytest.raises(ForecastReadUnavailableError, match="unavailable"):
            read(case, assignments=assignments)

    def test_foreign_group_refused_before_output_read(
        self, case: SimpleNamespace
    ) -> None:
        groups = SimpleNamespace(
            fetch_group=lambda group: SimpleNamespace(id=group, tenant_id=UUID(int=999))
        )
        with pytest.raises(ValueError, match="group"):
            read(case, stores=replace(case.stores, groups=groups))
        assert case.records.reads == []

    def test_nepal_scope_uses_same_selector(self, case: SimpleNamespace) -> None:
        settings = replace(case.settings, deployment="nepal21")
        key = replace(case.summary.claim.key, deployment="nepal21")
        summary = replace(case.summary, claim=replace(case.summary.claim, key=key))
        case.days.day = replace(case.days.day, latest=summary, authoritative=summary)
        assert (
            read(case, settings=settings).parameters[0].selection
            is EvaluationSelectionKind.CURRENT
        )

    def test_daily_snapshot_other_deployment_refused_before_values(
        self, case: SimpleNamespace
    ) -> None:
        with pytest.raises(ValueError, match="scope"):
            read(case, settings=replace(case.settings, deployment="other"))
        assert case.records.reads == []

    def test_non_midnight_scheduled_tick_same_issue_identity(
        self, case: SimpleNamespace
    ) -> None:
        context = ScheduledDailyForecast(
            scheduled_tick=ensure_utc(case.issue + timedelta(minutes=15))
        )
        summary = replace(
            case.summary,
            claim=replace(
                case.summary.claim, key=replace(case.summary.claim.key, context=context)
            ),
        )
        case.days.day = replace(case.days.day, latest=summary, authoritative=summary)
        assert read(case).parameters[0].selection is EvaluationSelectionKind.CURRENT

    def test_multi_group_does_not_replace_failed_member_with_other_group_success(
        self, case: SimpleNamespace
    ) -> None:
        first = case.settings.group_ids[0]
        second = StationGroupId(UUID(int=20))
        failed = replace(
            case.summary.outcomes[0],
            state=DailyMemberState.MODEL_FAILED,
            references=(),
            reason=DailyMemberReason.MODEL_FAILURE,
        )
        first_summary = replace(
            case.summary, state=DailyAttemptState.INCOMPLETE, outcomes=(failed,)
        )
        second_summary = replace(
            case.summary,
            claim=replace(
                case.summary.claim, key=replace(case.summary.claim.key, group_id=second)
            ),
        )

        class MultiDays:
            def read_day(self, key: DailyClaimKey) -> DailyDaySnapshot:
                summary = first_summary if key.group_id == first else second_summary
                if key.context.issue_time != case.issue:
                    return DailyDaySnapshot(
                        latest=None,
                        authoritative=None,
                        recovery_status=DailyRecoveryStatus.UNTOUCHED,
                    )
                return DailyDaySnapshot(
                    latest=summary,
                    authoritative=summary,
                    recovery_status=DailyRecoveryStatus.UNTOUCHED,
                )

        stores = replace(
            case.stores,
            daily=MultiDays(),
            groups=SimpleNamespace(
                fetch_group=lambda group: SimpleNamespace(
                    id=group, tenant_id=case.station.tenant_id
                )
            ),
        )
        result = read(
            case,
            stores=stores,
            settings=replace(case.settings, group_ids=(second, first)),
        )
        assert result.parameters[0].candidate is None
        assert result.daily[0].member.state is DailyMemberState.MODEL_FAILED
        assert result.daily[1].references[0].usability.value == "usable"

    def test_active_station_group_union_and_minimum_priority(
        self, case: SimpleNamespace
    ) -> None:
        from sapphire_flow.services.evaluation_forecasts import (
            resolve_evaluation_assignments,
        )
        from sapphire_flow.types.enums import ModelAssignmentStatus

        model = ModelId("persistence")
        active = ModelAssignmentStatus.ACTIVE
        station_store = SimpleNamespace(
            fetch_model_assignments=lambda _: [
                SimpleNamespace(
                    station_id=case.station.id,
                    model_id=model,
                    status=active,
                    priority=100,
                )
            ]
        )
        group = SimpleNamespace(
            id=StationGroupId(UUID(int=55)), tenant_id=case.station.tenant_id
        )
        group_store = SimpleNamespace(
            fetch_groups_for_station=lambda _: [group],
            fetch_group_model_assignments=lambda _: [
                SimpleNamespace(
                    group_id=group.id, model_id=model, status=active, priority=80
                )
            ],
        )
        assignments = resolve_evaluation_assignments(
            station=case.station,
            settings=case.settings,
            station_store=station_store,
            group_store=group_store,
            contracts={model: (case.contract,)},
        )
        assert [(a.model_id, a.priority) for a in assignments] == [(model, 80)]


class TestModelDiagnostics:
    def test_unselected_current_stale_absent_and_future_shadow(
        self, case: SimpleNamespace
    ) -> None:
        current, stale, absent = map(ModelId, ("other", "old", "missing"))
        row = replace(
            case.record,
            model_id=current,
            ensemble=replace(case.record.ensemble, model_id=current),
        )
        old_time = ensure_utc(case.issue - timedelta(days=30))
        old = replace(
            row,
            model_id=stale,
            issued_at=old_time,
            ensemble=replace(row.ensemble, model_id=stale, issued_at=old_time),
        )
        future = replace(row, issued_at=ensure_utc(case.issue + timedelta(hours=12)))
        case.records.records += [row, old, future]
        assignments = tuple(
            EvaluationAssignment(model_id=m, priority=i, contracts=())
            for i, m in enumerate((current, stale, absent))
        )
        result = read(case, assignments=assignments)
        assert [d.freshness.value for d in result.models] == [
            "current",
            "stale",
            "absent",
        ]
        assert result.models[0].records[0].record == row
        assert result.models[1].age == timedelta(days=30, hours=1)
        assert result.models[2].latest_result_at is None
        assert result.parameters[0].candidate.record.model_id == case.record.model_id

    def test_daily_unknown_is_not_absent_and_never_uses_ordinary_rows(
        self, case: SimpleNamespace
    ) -> None:
        case.days.day = replace(case.days.day, latest=None, authoritative=None)
        result = read(
            case,
            assignments=(
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=1,
                    contracts=(case.contract,),
                ),
            ),
        )
        assert result.models[0].freshness.value == "unknown"
        assert result.models[0].diagnostic_window_start is None
        assert result.models[0].evidence_authority.value == "stored_only_unverified"
        assert result.models[0].stored_result.output_id == case.record.id
        assert result.models[0].records == ()
        assert result.parameters[0].candidate is None

    def test_stale_daily_is_diagnostic_not_selected(
        self, case: SimpleNamespace
    ) -> None:
        result = read(
            case,
            as_of=case.issue + timedelta(days=1, hours=1),
            assignments=(
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=1,
                    contracts=(case.contract,),
                ),
            ),
        )
        assert result.models[0].freshness.value == "stale"
        assert result.models[0].records[0].record == case.record
        assert result.models[0].authoritative_attempt_id == case.summary.claim.id
        assert result.parameters[0].candidate is None

    def test_diagnostic_result_purpose_is_checked_without_contract(
        self, case: SimpleNamespace
    ) -> None:
        model = ModelId("other")
        case.records.fetch_latest_forecast_at = lambda *args: SimpleNamespace(
            data_use=ForecastDataUse.EXPIRED_RATING_TEST
        )
        from sapphire_flow.services.forecast_read import ForecastReadUnavailableError

        with pytest.raises(ForecastReadUnavailableError, match="unavailable"):
            read(
                case,
                assignments=(
                    EvaluationAssignment(model_id=model, priority=1, contracts=()),
                ),
            )

    def test_assignment_union_rejects_foreign_unconfigured_group(
        self, case: SimpleNamespace
    ) -> None:
        from sapphire_flow.services.evaluation_forecasts import (
            resolve_evaluation_assignments,
        )

        with pytest.raises(ValueError, match="tenant"):
            resolve_evaluation_assignments(
                station=case.station,
                settings=case.settings,
                contracts={},
                station_store=SimpleNamespace(fetch_model_assignments=lambda _: []),
                group_store=SimpleNamespace(
                    fetch_groups_for_station=lambda _: [
                        SimpleNamespace(id=UUID(int=99), tenant_id=UUID(int=999))
                    ]
                ),
            )


class TestDailyHistory:
    @staticmethod
    def assignments(case: SimpleNamespace) -> tuple[EvaluationAssignment, ...]:
        return (
            EvaluationAssignment(
                model_id=case.record.model_id, priority=1, contracts=()
            ),
        )

    @pytest.mark.parametrize("days", [3, 31])
    def test_finalized_result_survives_gap(
        self, case: SimpleNamespace, days: int
    ) -> None:
        result = read(
            case,
            as_of=case.issue + timedelta(days=days),
            assignments=self.assignments(case),
        )
        model = result.models[0]
        assert model.records[0].record == case.record
        assert model.latest_result_at == case.issue
        assert model.age == timedelta(days=days)
        assert model.evidence_authority.value == "finalized_summary"
        assert result.parameters[0].candidate is None

    @pytest.mark.parametrize("days", [0, 31])
    def test_legacy_identity_without_invented_contract_or_current_promotion(
        self, case: SimpleNamespace, days: int
    ) -> None:
        case.days.day = replace(case.days.day, latest=None, authoritative=None)
        result = read(
            case,
            as_of=case.issue + timedelta(days=days, hours=1),
            assignments=self.assignments(case),
        )
        model = result.models[0]
        assert model.stored_result.output_id == case.record.id
        assert model.latest_result_at == case.issue
        assert model.age == timedelta(days=days, hours=1)
        assert model.records == ()
        assert model.evidence_authority.value == "stored_only_unverified"
        assert model.freshness.value == "unknown"
        assert result.parameters[0].candidate is None

    def test_whole_summary_exclusion_does_not_revive_old_output(
        self, case: SimpleNamespace
    ) -> None:
        member = replace(
            case.summary.outcomes[0],
            state=DailyMemberState.MODEL_FAILED,
            references=(),
            reason=DailyMemberReason.MODEL_FAILURE,
        )
        summary = replace(
            case.summary, outcomes=(member,), state=DailyAttemptState.INCOMPLETE
        )
        case.days.day = replace(case.days.day, latest=summary, authoritative=summary)
        result = read(
            case,
            as_of=case.issue + timedelta(days=3),
            assignments=self.assignments(case),
        )
        assert result.models[0].records == ()
        assert result.models[0].stored_result is None
        assert result.models[0].authoritative_attempt_id == summary.claim.id

    @pytest.mark.parametrize("damage", ["future", "shadow", "superseded"])
    def test_ineligible_newer_rows_do_not_shadow_latest(
        self, case: SimpleNamespace, damage: str
    ) -> None:
        other = replace(
            case.record,
            id=ForecastId(UUID(int=999)),
            issued_at=ensure_utc(case.issue + timedelta(days=1)),
        )
        if damage == "shadow":
            from sapphire_flow.types.forecast_lineage import ForecastInputLineage

            other = replace(
                other,
                data_use=ForecastDataUse.EXPIRED_RATING_TEST,
                input_lineage=ForecastInputLineage(
                    provisional_discharge_fingerprints=("a" * 64,),
                    transformation_versions=("v1",),
                ),
            )
        elif damage == "superseded":
            other = replace(other, status=ForecastStatus.SUPERSEDED)
        case.records.records.append(other)
        now = case.issue if damage == "future" else case.issue + timedelta(days=3)
        result = read(case, as_of=now, assignments=self.assignments(case))
        assert result.models[0].records[0].record.id == case.record.id

    def test_newer_rejection_is_not_hidden_by_accepted_legacy(
        self, case: SimpleNamespace
    ) -> None:
        case.days.day = replace(case.days.day, latest=None, authoritative=None)
        issue = ensure_utc(case.issue + timedelta(days=1))
        rejection = PersistedRejectedForecast(
            id=UUID(int=888),
            attempt_id=UUID(int=887),
            recorded_at=issue,
            station_id=case.station.id,
            model_id=case.record.model_id,
            model_artifact_id=None,
            group_id=case.settings.group_ids[0],
            issued_at=issue,
            parameter="discharge",
            representation=case.record.representation,
            units=case.record.ensemble.units,
            time_step_seconds=3600,
            qc_status=QcStatus.QC_FAILED,
            qc_flags=(),
            values={},
        )
        case.stores.rejected.records.append(rejection)
        result = read(
            case,
            as_of=case.issue + timedelta(days=3),
            assignments=self.assignments(case),
        )
        assert result.models[0].stored_result.output_id == rejection.id
        assert result.models[0].stored_result.source is DailyOutputSource.REJECTED
        assert result.models[0].latest_result_at == issue
        assert result.models[0].age == timedelta(days=2)
        assert result.models[0].records == ()
        assert result.parameters[0].candidate is None


class TestDailyAbsenceWithFallback:
    def test_coverage_miss_remains_visible_when_fallback_covers(
        self, case: SimpleNamespace
    ) -> None:
        model = ModelId("fallback")
        record = replace(
            case.record,
            id=ForecastId(UUID(int=900)),
            model_id=model,
            ensemble=replace(
                case.record.ensemble,
                model_id=model,
                values=case.record.ensemble.values.with_columns(
                    (pl.col("valid_time") + timedelta(hours=2)).alias("valid_time")
                ),
            ),
        )
        case.records.records.append(record)
        result = read(
            case,
            valid_at=case.issue + timedelta(hours=3),
            assignments=(
                EvaluationAssignment(
                    model_id=model,
                    priority=1,
                    contracts=(replace(case.contract, first_lead=timedelta(hours=3)),),
                ),
            ),
        )
        assert result.parameters[0].selection is EvaluationSelectionKind.FALLBACK
        assert result.parameters[0].absence is None
        assert result.parameters[0].daily_absence.value == "no_valid_time_coverage"


class TestConcurrentLedgerTransitions:
    @pytest.mark.parametrize("transition", ["claim", "finish", "retry"])
    def test_post_as_of_keeps_ordinary_fallback(
        self, case: SimpleNamespace, transition: str
    ) -> None:
        now = case.issue + timedelta(hours=1)
        future = now + timedelta(microseconds=1)
        snapshot = replace(case.summary, finished_at=future)
        if transition in ("claim", "retry"):
            snapshot = replace(
                snapshot,
                claim=replace(snapshot.claim, started_at=future),
                state=DailyAttemptState.ACTIVE,
                outcomes=None,
                finished_at=None,
            )
        case.days.day = replace(
            case.days.day,
            latest=snapshot,
            authoritative=case.summary
            if transition == "retry"
            else (snapshot if transition == "finish" else None),
        )
        model = ModelId("ordinary")
        ordinary = replace(
            case.record,
            model_id=model,
            ensemble=replace(case.record.ensemble, model_id=model),
        )
        case.records.records.append(ordinary)
        result = read(
            case,
            as_of=now,
            assignments=(
                EvaluationAssignment(
                    model_id=model, priority=1, contracts=(case.contract,)
                ),
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=2,
                    contracts=(case.contract,),
                ),
            ),
        )
        assert result.parameters[0].selection is EvaluationSelectionKind.FALLBACK
        assert result.parameters[0].candidate.record == ordinary
        diagnostic = result.daily[0]
        assert diagnostic.availability.value == "post_as_of"
        assert diagnostic.latest_state is None
        assert diagnostic.latest_attempt_id is None
        assert diagnostic.authoritative_attempt_id is None
        assert diagnostic.member is None
        assert diagnostic.recovery_status is None
        assert result.models[1].records == ()
        assert result.models[1].availability.value == "post_as_of"

    def test_future_foreign_scope_still_fails(self, case: SimpleNamespace) -> None:
        future = replace(
            case.summary,
            finished_at=case.issue + timedelta(hours=2),
            claim=replace(
                case.summary.claim,
                key=replace(case.summary.claim.key, deployment="foreign"),
            ),
        )
        case.days.day = replace(case.days.day, latest=future)
        with pytest.raises(ValueError, match="scope"):
            read(case)

    def test_historical_transition_does_not_infer_history(
        self, case: SimpleNamespace
    ) -> None:
        now = case.issue + timedelta(days=3)
        future = replace(case.summary, finished_at=now + timedelta(seconds=1))
        case.days.day = replace(case.days.day, latest=future, authoritative=future)
        result = read(
            case,
            as_of=now,
            assignments=(
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=1,
                    contracts=(case.contract,),
                ),
            ),
        )
        assert result.models[0].availability.value == "post_as_of"
        assert result.models[0].stored_result is None
        assert result.models[0].records == ()


def failed_diagnostic_case(case: SimpleNamespace, unrelated_summary: bool) -> None:
    """Failed A evidence must not borrow finalized B authority."""
    from sapphire_flow.types.daily_forecast_claim import DailyFailureReason
    from sapphire_flow.types.ids import StationId

    failed = replace(
        case.summary,
        state=DailyAttemptState.FAILED,
        outcomes=None,
        failure_reason=DailyFailureReason.MODEL_FAILURE,
    )
    other_group = StationGroupId(UUID(int=40))
    other_member = replace(
        case.summary.claim.frozen.members[0], station_id=StationId(UUID(int=41))
    )
    other = replace(
        case.summary,
        claim=replace(
            case.summary.claim,
            id=UUID(int=42),
            key=replace(case.summary.claim.key, group_id=other_group),
            frozen=replace(case.summary.claim.frozen, members=(other_member,)),
        ),
        outcomes=(
            replace(
                other_member,
                state=DailyMemberState.MODEL_FAILED,
                reason=DailyMemberReason.MODEL_FAILURE,
            ),
        ),
        state=DailyAttemptState.INCOMPLETE,
    )
    empty = DailyDaySnapshot(
        latest=None, authoritative=None, recovery_status=DailyRecoveryStatus.UNTOUCHED
    )
    reads = []

    def read_day(key: DailyClaimKey) -> DailyDaySnapshot:
        reads.append(key)
        if key.context.issue_time != case.issue:
            return empty
        if key.group_id == failed.claim.key.group_id:
            return replace(empty, latest=failed)
        return (
            replace(empty, latest=other, authoritative=other)
            if unrelated_summary
            else empty
        )

    case.settings = replace(
        case.settings, group_ids=(failed.claim.key.group_id, other_group)
    )
    case.days = SimpleNamespace(read_day=read_day, reads=reads)
    case.stores = replace(
        case.stores,
        daily=case.days,
        groups=SimpleNamespace(
            fetch_group=lambda group_id: SimpleNamespace(
                id=group_id, tenant_id=case.station.tenant_id
            )
        ),
    )
    case.records.records = []
    case.stores.rejected.records = [
        PersistedRejectedForecast(
            id=UUID(int=888),
            attempt_id=failed.claim.id,
            recorded_at=case.issue,
            station_id=case.station.id,
            model_id=case.record.model_id,
            model_artifact_id=failed.claim.frozen.artifact_id,
            group_id=failed.claim.key.group_id,
            issued_at=case.issue,
            parameter="discharge",
            representation=case.record.representation,
            units=case.record.ensemble.units,
            time_step_seconds=3600,
            qc_status=QcStatus.QC_FAILED,
            qc_flags=(
                QcFlag(
                    rule_id="range_check",
                    rule_version="1",
                    status=QcStatus.QC_FAILED,
                    detail="private failed evidence",
                ),
            ),
            values={"0": ((case.issue, -123.0),)},
        )
    ]


class TestFailedDiagnosticAttribution:
    @pytest.mark.parametrize("unrelated_summary", [False, True])
    @pytest.mark.parametrize("days", [0, 3])
    @pytest.mark.parametrize("linked", [False, True])
    def test_failed_evidence_never_borrows_other_group_authority(
        self, case: SimpleNamespace, unrelated_summary: bool, days: int, linked: bool
    ) -> None:
        failed_diagnostic_case(case, unrelated_summary)
        if not linked:
            case.stores.rejected.records[0] = replace(
                case.stores.rejected.records[0], attempt_id=UUID(int=999)
            )
        result = read(
            case,
            as_of=case.issue + timedelta(days=days, hours=1),
            assignments=(
                EvaluationAssignment(
                    model_id=case.record.model_id,
                    priority=1,
                    contracts=(case.contract,),
                ),
            ),
        )
        diagnostic = result.models[0]
        assert diagnostic.evidence_authority.value == "stored_only_unverified"
        assert diagnostic.authoritative_attempt_id is None
        assert diagnostic.latest_attempt_id == (
            case.summary.claim.id if linked else None
        )
        assert diagnostic.group_id == (case.settings.group_ids[0] if linked else None)
        assert diagnostic.latest_state == (DailyAttemptState.FAILED if linked else None)
        assert diagnostic.stored_result.output_id == UUID(int=888)
        assert diagnostic.records == ()
        assert result.parameters[0].candidate is None
        assert len(case.days.reads) == 6  # two recent days + one exact history day
        assert case.records.reads == []
