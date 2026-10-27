from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import polars as pl

from sapphire_flow.services.daily_output_integrity import assess_daily_output
from sapphire_flow.services.forecast_read import (
    require_standard_forecast_results,
    require_standard_forecast_store,
)
from sapphire_flow.types.daily_forecast import DailyAttemptState, ScheduledDailyForecast
from sapphire_flow.types.daily_forecast_claim import DailyClaimKey
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_output import DailyOutputEligibility
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    ModelAssignmentStatus,
    QcStatus,
)
from sapphire_flow.types.evaluation_forecast import (
    EvaluationAbsence,
    EvaluationAssignment,
    EvaluationCandidate,
    EvaluationDailyDiagnostic,
    EvaluationDiagnosticAvailability,
    EvaluationEvidenceAuthority,
    EvaluationForecasts,
    EvaluationFreshness,
    EvaluationModelDiagnostic,
    EvaluationParameter,
    EvaluationReferenceDiagnostic,
    EvaluationReferenceUsability,
    EvaluationSelectionKind,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ForecastId, RejectedForecastId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.types.ids import ModelId

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import (
        ForecastStore,
        RejectedForecastStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.daily_forecast_snapshot import (
        DailyAttemptSnapshot,
        DailyDaySnapshot,
    )
    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.evaluation_forecast import (
        EvaluationReadSettings,
    )
    from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast
    from sapphire_flow.types.station import StationConfig


class DailyEvaluationReader(Protocol):
    def read_day(self, key: DailyClaimKey) -> DailyDaySnapshot: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationStores:
    daily: DailyEvaluationReader
    forecasts: ForecastStore
    rejected: RejectedForecastStore
    groups: StationGroupStore


def read_evaluation_forecasts(
    *,
    station: StationConfig,
    settings: EvaluationReadSettings,
    assignments: tuple[EvaluationAssignment, ...],
    stores: EvaluationStores,
    as_of: datetime,
    valid_at: datetime | None = None,
) -> EvaluationForecasts:
    """Caller authorizes the station and supplies declared active assignments.

    Returned records are internal, not publication-filtered HTTP responses.
    """
    now = ensure_utc(as_of)
    valid = ensure_utc(valid_at) if valid_at is not None else now
    issue = ScheduledDailyForecast(scheduled_tick=now).issue_time
    if station.tenant_id != settings.tenant_id:
        raise ValueError("evaluation station is outside configured tenant")
    require_standard_forecast_store(stores.forecasts)
    require_standard_forecast_store(stores.rejected)
    # Check every group before any ledger/output read, including removed members.
    for group_id in settings.group_ids:
        group = stores.groups.fetch_group(group_id)
        if (
            group is None
            or group.id != group_id
            or group.tenant_id != settings.tenant_id
        ):
            raise ValueError("evaluation group is outside configured tenant")
    diagnostics: list[EvaluationDailyDiagnostic] = []
    current: dict[str, EvaluationCandidate] = {}
    uncovered: set[str] = set()
    parameters = list(settings.parameters)
    chosen_group = None
    daily_model: EvaluationModelDiagnostic | None = None
    candidate_cache: dict[object, EvaluationCandidate | None] = {}
    window_start = ensure_utc(issue - timedelta(days=1))
    keys = [
        (day_issue, group_id)
        for day_issue in (issue, window_start)
        for group_id in sorted(settings.group_ids, key=str)
    ]
    for day_issue, group_id in keys:
        key = DailyClaimKey(
            tenant_id=settings.tenant_id,
            deployment=settings.deployment,
            group_id=group_id,
            model_id=settings.daily_model_id,
            context=ScheduledDailyForecast(scheduled_tick=day_issue),
        )
        day = stores.daily.read_day(key)
        if not _day_available(day, key, now):
            diagnostics.append(
                EvaluationDailyDiagnostic(
                    group_id=group_id,
                    issue_time=day_issue,
                    latest_attempt_id=None,
                    latest_state=None,
                    failure_reason=None,
                    authoritative_attempt_id=None,
                    member=None,
                    recovery_status=None,
                    availability=EvaluationDiagnosticAvailability.POST_AS_OF,
                )
            )
            if daily_model is None:
                daily_model = EvaluationModelDiagnostic(
                    model_id=settings.daily_model_id,
                    freshness=EvaluationFreshness.UNKNOWN,
                    latest_result_at=None,
                    age=None,
                    records=(),
                    diagnostic_window_start=window_start,
                    availability=EvaluationDiagnosticAvailability.POST_AS_OF,
                )
            continue
        summary = day.authoritative
        member = (
            next(
                (m for m in summary.outcomes or () if m.station_id == station.id), None
            )
            if summary
            else None
        )
        reference_status: dict[DailyOutputReference, EvaluationReferenceUsability] = {}
        if summary is not None and member is not None:
            for ref in member.references:
                candidate_cache[ref] = _daily_candidate(
                    stores, summary, ref, now, settings, reference_status
                )
        diagnostics.append(
            EvaluationDailyDiagnostic(
                group_id=group_id,
                issue_time=day_issue,
                latest_attempt_id=day.latest.claim.id if day.latest else None,
                latest_state=day.latest.state if day.latest else None,
                failure_reason=day.latest.failure_reason if day.latest else None,
                authoritative_attempt_id=summary.claim.id if summary else None,
                member=member,
                recovery_status=day.recovery_status,
                references=tuple(
                    EvaluationReferenceDiagnostic(reference=ref, usability=status)
                    for ref, status in reference_status.items()
                ),
            )
        )
        if (
            daily_model is None
            and any(a.model_id == settings.daily_model_id for a in assignments)
            and day.latest is not None
            and any(m.station_id == station.id for m in day.latest.claim.frozen.members)
        ):
            diagnostic_records: list[EvaluationCandidate] = []
            if summary is not None and member is not None:
                for ref in member.references:
                    diagnostic_candidate = candidate_cache[ref]
                    if diagnostic_candidate is not None:
                        diagnostic_records.append(diagnostic_candidate)
            result_at = day.latest.finished_at or day.latest.claim.started_at
            daily_model = EvaluationModelDiagnostic(
                model_id=settings.daily_model_id,
                freshness=EvaluationFreshness.CURRENT
                if day_issue == issue
                else EvaluationFreshness.STALE,
                latest_result_at=result_at,
                age=now - result_at,
                records=tuple(diagnostic_records),
                diagnostic_window_start=window_start,
                evidence_authority=EvaluationEvidenceAuthority.FINALIZED_SUMMARY
                if summary
                else EvaluationEvidenceAuthority.UNKNOWN,
                group_id=group_id,
                issue_time=day_issue,
                latest_attempt_id=day.latest.claim.id,
                latest_state=day.latest.state,
                authoritative_attempt_id=summary.claim.id if summary else None,
            )
        if day_issue != issue or chosen_group is not None:
            continue
        if member is not None:
            chosen_group = group_id
            parameters.extend(
                p for p in member.required_parameters if p not in parameters
            )
        if (
            summary is None
            or member is None
            or member.state is not DailyMemberState.PRODUCED
        ):
            continue
        for ref in member.references:
            if ref.key.parameter not in parameters:
                continue
            if ref not in candidate_cache:
                candidate_cache[ref] = _daily_candidate(
                    stores, summary, ref, now, settings
                )
            candidate = candidate_cache[ref]
            if candidate is None:
                continue
            if _covers(candidate, valid):
                current.setdefault(ref.key.parameter, candidate)
            else:
                uncovered.add(ref.key.parameter)
    if any(a.model_id == settings.daily_model_id for a in assignments) and (
        daily_model is None
        or daily_model.availability is EvaluationDiagnosticAvailability.AVAILABLE
    ):
        daily_model = _daily_history(stores, station, settings, now, daily_model)
    selected: list[EvaluationParameter] = []
    ordered = tuple(sorted(assignments, key=lambda a: (a.priority, a.model_id)))
    for parameter in parameters:
        candidate = current.get(parameter)
        kind = EvaluationSelectionKind.CURRENT
        daily_absence = (
            None
            if candidate
            else (
                EvaluationAbsence.NO_VALID_TIME_COVERAGE
                if parameter in uncovered
                else EvaluationAbsence.NO_USABLE_OUTPUT
            )
        )
        if candidate is None:
            candidate, missed = _fallback(
                stores, station, settings, ordered, parameter, issue, now, valid
            )
            if missed:
                uncovered.add(parameter)
            kind = EvaluationSelectionKind.FALLBACK
        selected.append(
            EvaluationParameter(
                parameter=parameter,
                selection=kind if candidate else EvaluationSelectionKind.ABSENT,
                candidate=candidate,
                daily_absence=daily_absence,
                absence=None
                if candidate
                else (
                    EvaluationAbsence.NO_VALID_TIME_COVERAGE
                    if parameter in uncovered
                    else EvaluationAbsence.NO_USABLE_OUTPUT
                ),
            )
        )
    return EvaluationForecasts(
        as_of=now,
        valid_at=valid,
        issue_time=issue,
        parameters=tuple(selected),
        daily=tuple(diagnostics),
        assignments=ordered,
        models=tuple(
            (
                daily_model
                or EvaluationModelDiagnostic(
                    model_id=a.model_id,
                    freshness=EvaluationFreshness.UNKNOWN,
                    latest_result_at=None,
                    age=None,
                    records=(),
                    diagnostic_window_start=window_start,
                )
            )
            if a.model_id == settings.daily_model_id
            else _ordinary_diagnostic(stores, station, a, issue, now)
            for a in ordered
        ),
        diagnostic_window_start=window_start,
    )


def _day_available(day: DailyDaySnapshot, key: DailyClaimKey, now: UtcDatetime) -> bool:
    snapshots = tuple(s for s in (day.latest, day.authoritative) if s is not None)
    if any(
        s.claim.key.tenant_id != key.tenant_id
        or s.claim.key.deployment != key.deployment
        or s.claim.key.group_id != key.group_id
        or s.claim.key.model_id != key.model_id
        or s.claim.key.data_use != key.data_use
        or s.claim.key.context.issue_time != key.context.issue_time
        for s in snapshots
    ):
        raise ValueError("daily snapshot is outside requested scope")
    if day.authoritative is not None and day.authoritative.state not in (
        DailyAttemptState.COMPLETE,
        DailyAttemptState.INCOMPLETE,
    ):
        raise ValueError("daily authority must be a finalized whole summary")
    # No transition history is available. Do not invent an earlier state.
    return not any(
        s.claim.started_at > now
        or (s.finished_at is not None and s.finished_at > now)
        or (s.recovery is not None and s.recovery.confirmed_at > now)
        for s in snapshots
    )


def _daily_history(
    stores: EvaluationStores,
    station: StationConfig,
    settings: EvaluationReadSettings,
    now: UtcDatetime,
    recent: EvaluationModelDiagnostic | None,
) -> EvaluationModelDiagnostic | None:
    # Two bounded SQL result reads, never a history scan or an acceptance path.
    accepted = stores.forecasts.fetch_latest_forecast_at(
        station.id, settings.daily_model_id, now
    )
    rejected = stores.rejected.fetch_latest_rejected_forecast_at(
        station.id, settings.daily_model_id, now, settings.group_ids
    )
    rows = tuple(r for r in (accepted, rejected) if r is not None)
    require_standard_forecast_results(rows)
    for row in rows:
        if (
            row.station_id != station.id
            or row.model_id != settings.daily_model_id
            or row.issued_at > now
        ):
            raise ValueError("stored daily diagnostic is outside requested scope")
        if isinstance(row, OperationalForecast):
            if (
                row.status is ForecastStatus.SUPERSEDED
                or row.combination_strategy is not None
            ):
                raise ValueError("stored daily diagnostic is superseded or combined")
        elif row.group_id is not None and row.group_id not in settings.group_ids:
            raise ValueError("stored daily diagnostic is outside group scope")
    if not rows:
        return recent
    # A rejection wins an equal-issue tie: never describe an older accepted row
    # as latest while concealing an unresolved rejection at the same issue.
    row = max(rows, key=lambda r: (r.issued_at, not isinstance(r, OperationalForecast)))
    issue = ScheduledDailyForecast(scheduled_tick=row.issued_at).issue_time
    if (
        recent is not None
        and recent.issue_time == row.issued_at
        and recent.evidence_authority is EvaluationEvidenceAuthority.FINALIZED_SUMMARY
    ):
        return recent
    summaries: list[DailyAttemptSnapshot] = []
    linked_attempt: DailyAttemptSnapshot | None = None
    for group_id in sorted(settings.group_ids, key=str):
        key = DailyClaimKey(
            tenant_id=settings.tenant_id,
            deployment=settings.deployment,
            group_id=group_id,
            model_id=settings.daily_model_id,
            context=ScheduledDailyForecast(scheduled_tick=issue),
        )
        day = stores.daily.read_day(key)
        if not _day_available(day, key, now):
            return EvaluationModelDiagnostic(
                model_id=settings.daily_model_id,
                freshness=EvaluationFreshness.UNKNOWN,
                latest_result_at=None,
                age=None,
                records=(),
                diagnostic_window_start=None,
                availability=EvaluationDiagnosticAvailability.POST_AS_OF,
            )
        if (
            not isinstance(row, OperationalForecast)
            and day.latest is not None
            and row.attempt_id == day.latest.claim.id
            and row.group_id == group_id
            and row.issued_at == issue
            and row.model_artifact_id == day.latest.claim.frozen.artifact_id
            and any(m.station_id == station.id for m in day.latest.claim.frozen.members)
        ):
            linked_attempt = day.latest
        summary = day.authoritative
        if summary is None:
            continue
        if summary.finished_at is None or summary.state not in (
            DailyAttemptState.COMPLETE,
            DailyAttemptState.INCOMPLETE,
        ):
            raise ValueError("historical daily summary is outside scope or clock")
        summaries.append(summary)
    summary = next(
        (
            s
            for s in summaries
            if any(m.station_id == station.id for m in s.outcomes or ())
            and (
                isinstance(row, OperationalForecast)
                or row.group_id == s.claim.key.group_id
            )
        ),
        None,
    )
    if summary is not None and row.issued_at == issue:
        # Only this station's whole summary can replace its stored diagnostic.
        member = next(
            (m for m in summary.outcomes or () if m.station_id == station.id), None
        )
        records = tuple(
            c
            for ref in (member.references if member else ())
            if (c := _daily_candidate(stores, summary, ref, now, settings)) is not None
        )
        return EvaluationModelDiagnostic(
            model_id=settings.daily_model_id,
            freshness=EvaluationFreshness.STALE,
            latest_result_at=summary.claim.key.context.issue_time,
            age=now - summary.claim.key.context.issue_time,
            records=records,
            diagnostic_window_start=None,
            evidence_authority=EvaluationEvidenceAuthority.FINALIZED_SUMMARY,
            group_id=summary.claim.key.group_id,
            issue_time=issue,
            authoritative_attempt_id=summary.claim.id,
        )
    # Legacy/unverified rows have no frozen contract. Expose identity and age,
    # not inferred shape, acceptance, values, or current eligibility.
    ref = DailyOutputReference(
        key=ForecastOutputKey(
            station_id=station.id,
            model_id=settings.daily_model_id,
            issued_at=row.issued_at,
            parameter=row.ensemble.parameter
            if isinstance(row, OperationalForecast)
            else row.parameter,
        ),
        source=DailyOutputSource.FORECAST
        if isinstance(row, OperationalForecast)
        else DailyOutputSource.REJECTED,
        output_id=row.id,
    )
    return EvaluationModelDiagnostic(
        model_id=settings.daily_model_id,
        freshness=EvaluationFreshness.UNKNOWN,
        latest_result_at=row.issued_at,
        age=now - row.issued_at,
        records=(),
        diagnostic_window_start=None,
        evidence_authority=EvaluationEvidenceAuthority.STORED_ONLY_UNVERIFIED,
        stored_result=ref,
        issue_time=row.issued_at,
        group_id=linked_attempt.claim.key.group_id if linked_attempt else None,
        latest_attempt_id=linked_attempt.claim.id if linked_attempt else None,
        latest_state=linked_attempt.state if linked_attempt else None,
    )


def _ordinary_diagnostic(
    stores: EvaluationStores,
    station: StationConfig,
    assignment: EvaluationAssignment,
    issue: UtcDatetime,
    now: UtcDatetime,
) -> EvaluationModelDiagnostic:
    record = stores.forecasts.fetch_latest_forecast_at(
        station.id, assignment.model_id, now
    )
    if record is None:
        return EvaluationModelDiagnostic(
            model_id=assignment.model_id,
            freshness=EvaluationFreshness.ABSENT,
            latest_result_at=None,
            age=None,
            records=(),
            diagnostic_window_start=None,
        )
    require_standard_forecast_results((record,))
    if (
        record.station_id != station.id
        or record.model_id != assignment.model_id
        or record.issued_at > now
        or record.status is ForecastStatus.SUPERSEDED
        or record.combination_strategy is not None
    ):
        raise ValueError("latest diagnostic is outside requested scope")
    candidate = EvaluationCandidate(
        reference=DailyOutputReference(
            key=ForecastOutputKey(
                station_id=station.id,
                model_id=assignment.model_id,
                issued_at=record.issued_at,
                parameter=record.ensemble.parameter,
            ),
            source=DailyOutputSource.FORECAST,
            output_id=record.id,
        ),
        record=record,
        age=now - record.issued_at,
    )
    return EvaluationModelDiagnostic(
        model_id=assignment.model_id,
        freshness=EvaluationFreshness.CURRENT
        if record.issued_at >= issue
        else EvaluationFreshness.STALE,
        latest_result_at=record.issued_at,
        age=candidate.age,
        records=(candidate,),
        diagnostic_window_start=None,
    )


def _daily_candidate(
    stores: EvaluationStores,
    summary: DailyAttemptSnapshot,
    ref: DailyOutputReference,
    now: UtcDatetime,
    settings: EvaluationReadSettings,
    reference_status: dict[DailyOutputReference, EvaluationReferenceUsability]
    | None = None,
) -> EvaluationCandidate | None:
    if reference_status is not None:
        reference_status[ref] = EvaluationReferenceUsability.UNAVAILABLE
    claim = summary.claim
    contract = next(
        (c for c in claim.frozen.output_contracts if c.parameter == ref.key.parameter),
        None,
    )
    if contract is None or claim.frozen.artifact_id is None:
        return None
    record = (
        stores.forecasts.fetch_forecast(ForecastId(ref.output_id))
        if ref.source is DailyOutputSource.FORECAST
        else stores.rejected.fetch_rejected_forecast(RejectedForecastId(ref.output_id))
    )
    if record is None:
        return None
    require_standard_forecast_results((record,))
    if (ref.source is DailyOutputSource.FORECAST) != isinstance(
        record, OperationalForecast
    ):
        return None
    if (
        record.id != ref.output_id
        or record.station_id != ref.key.station_id
        or record.model_id != ref.key.model_id
        or record.issued_at != ref.key.issued_at
        or record.issued_at > now
        or record.model_artifact_id != claim.frozen.artifact_id
    ):
        return None
    if isinstance(record, OperationalForecast):
        if record.status is ForecastStatus.SUPERSEDED:
            if reference_status is not None:
                reference_status[ref] = EvaluationReferenceUsability.SUPERSEDED
            return None
        if record.qc_status not in (
            QcStatus.QC_PASSED,
            QcStatus.QC_SUSPECT,
        ):
            return None
        ensemble = record.ensemble
    else:
        if (
            record.attempt_id != claim.id
            or record.group_id != claim.key.group_id
            or record.parameter != ref.key.parameter
        ):
            return None
        ensemble = _rejected_ensemble(record)
        if ensemble is None:
            return None
    if not _usable(ensemble, ref.key, contract, record, settings):
        return None
    if reference_status is not None:
        reference_status[ref] = EvaluationReferenceUsability.USABLE
    return EvaluationCandidate(reference=ref, record=record, age=now - record.issued_at)


def _rejected_ensemble(record: PersistedRejectedForecast) -> ForecastEnsemble | None:
    # Horizon is measured only to support declared AT_MOST bounds; expected cells,
    # levels, cadence, units and minimum/maximum still come from the frozen contract.
    try:
        series = (
            "member_id"
            if record.representation is EnsembleRepresentation.MEMBERS
            else "quantile"
        )
        rows = [
            (t, float(k), v) for k, values in record.values.items() for t, v in values
        ]
        if not rows:
            return None
        frame = pl.DataFrame(rows, schema=["valid_time", series, "value"], orient="row")
        return ForecastEnsemble(
            station_id=record.station_id,
            model_id=record.model_id,
            issued_at=record.issued_at,
            parameter=record.parameter,
            units=record.units,
            representation=record.representation,
            values=frame,
            time_step=timedelta(seconds=record.time_step_seconds),
            forecast_horizon_steps=frame["valid_time"].n_unique(),
        )
    except (ValueError, TypeError, OverflowError, pl.exceptions.PolarsError):
        return None


def _usable(
    ensemble: ForecastEnsemble,
    key: ForecastOutputKey,
    contract: DailyOutputContract,
    record: OperationalForecast | PersistedRejectedForecast,
    settings: EvaluationReadSettings,
) -> bool:
    if any(
        f.status not in (QcStatus.QC_PASSED, QcStatus.QC_SUSPECT, QcStatus.QC_FAILED)
        for f in record.qc_flags
    ):
        return False
    try:
        eligibility = assess_daily_output(
            ensemble, key, contract, record.qc_status, record.qc_flags
        )
    except (ValueError, TypeError, KeyError, OverflowError, pl.exceptions.PolarsError):
        return False
    return eligibility is DailyOutputEligibility.USABLE or (
        eligibility is DailyOutputEligibility.PLAUSIBILITY_FLAGGED
        and key.model_id in settings.plausibility_models
    )


def _covers(candidate: EvaluationCandidate, valid: UtcDatetime) -> bool:
    record = candidate.record
    if isinstance(record, OperationalForecast):
        times = record.ensemble.values["valid_time"].to_list()
        step = record.ensemble.time_step
    else:
        times = [t for values in record.values.values() for t, _ in values]
        step = timedelta(seconds=record.time_step_seconds)
    return any(t <= valid < t + step for t in times)


def _fallback(
    stores: EvaluationStores,
    station: StationConfig,
    settings: EvaluationReadSettings,
    assignments: tuple[EvaluationAssignment, ...],
    parameter: str,
    issue: UtcDatetime,
    now: UtcDatetime,
    valid: UtcDatetime,
) -> tuple[EvaluationCandidate | None, bool]:
    missed = False
    for assignment in assignments:
        if assignment.model_id == settings.daily_model_id:
            continue  # Never bypass the daily whole-summary authority.
        contract = next(
            (c for c in assignment.contracts if c.parameter == parameter), None
        )
        if contract is None:
            continue
        records = stores.forecasts.fetch_forecasts_in_range(
            station.id,
            issue,
            ensure_utc(issue + timedelta(days=1)),
            model_id=assignment.model_id,
            parameter=parameter,
        )
        require_standard_forecast_results(records)
        eligible = [
            r
            for r in records
            if r.station_id == station.id
            and r.model_id == assignment.model_id
            and issue <= r.issued_at <= now
            and r.status is not ForecastStatus.SUPERSEDED
            and r.ensemble.parameter == parameter
            and r.combination_strategy is None
            and r.qc_status in (QcStatus.QC_PASSED, QcStatus.QC_SUSPECT)
        ]
        for record in sorted(
            eligible, key=lambda r: (r.issued_at, r.version, str(r.id)), reverse=True
        ):
            key = ForecastOutputKey(
                station_id=station.id,
                model_id=assignment.model_id,
                issued_at=record.issued_at,
                parameter=parameter,
            )
            if not _usable(record.ensemble, key, contract, record, settings):
                continue
            candidate = EvaluationCandidate(
                reference=DailyOutputReference(
                    key=key, source=DailyOutputSource.FORECAST, output_id=record.id
                ),
                record=record,
                age=now - record.issued_at,
            )
            if _covers(candidate, valid):
                return candidate, missed
            missed = True
            break  # Latest usable issue for this model, not an older covering issue.
    return None, missed


def resolve_evaluation_assignments(
    *,
    station: StationConfig,
    settings: EvaluationReadSettings,
    station_store: StationStore,
    group_store: StationGroupStore,
    contracts: Mapping[ModelId, tuple[DailyOutputContract, ...]],
) -> tuple[EvaluationAssignment, ...]:
    """Resolve active priorities; missing contracts remain visible but unusable."""
    if station.tenant_id != settings.tenant_id:
        raise ValueError("evaluation station is outside configured tenant")
    priorities: dict[ModelId, int] = {}
    for assignment in station_store.fetch_model_assignments(station.id):
        if assignment.station_id != station.id:
            raise ValueError("evaluation assignment has wrong station")
        if assignment.status is ModelAssignmentStatus.ACTIVE:
            priorities[assignment.model_id] = min(
                priorities.get(assignment.model_id, assignment.priority),
                assignment.priority,
            )
    for group in group_store.fetch_groups_for_station(station.id):
        if group.tenant_id != settings.tenant_id:
            raise ValueError("evaluation assignment group has wrong tenant")
        for assignment in group_store.fetch_group_model_assignments(group.id):
            if assignment.group_id != group.id:
                raise ValueError("evaluation assignment has wrong group")
            if assignment.status is ModelAssignmentStatus.ACTIVE:
                priorities[assignment.model_id] = min(
                    priorities.get(assignment.model_id, assignment.priority),
                    assignment.priority,
                )
    return tuple(
        EvaluationAssignment(
            model_id=model, priority=priority, contracts=contracts.get(model, ())
        )
        for model, priority in sorted(
            priorities.items(), key=lambda item: (item[1], item[0])
        )
    )
