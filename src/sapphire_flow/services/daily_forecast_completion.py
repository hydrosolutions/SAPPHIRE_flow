from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from sapphire_flow.exceptions import (
    DailyOutputRolledBackError,
    ModelOutputError,
    StoreError,
)
from sapphire_flow.services.daily_forecast_persistence import (
    commit_daily_forecasts,
    commit_daily_rejection,
    validate_daily_forecasts,
    validate_daily_rejection,
)
from sapphire_flow.services.daily_output_integrity import assess_daily_output
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberReason,
    DailyMemberState,
    DailyOutputSource,
)
from sapphire_flow.types.daily_output import DailyOutputEligibility
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedParameterPayload,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sapphire_flow.protocols.stores import (
        CommittedForecastWriter,
        CommittedRejectedForecastWriter,
    )
    from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
    from sapphire_flow.types.daily_forecast import DailyAttemptState
    from sapphire_flow.types.daily_forecast_one_shot import DailyExecution
    from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
    from sapphire_flow.types.daily_output import DailyOutputEvidence
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class CompletedDailyGroup:
    state: DailyAttemptState
    outcomes: tuple[DailyMemberOutcome, ...]


class DailyCompletionStore(Protocol):
    def finalize(
        self,
        claim: DailyExecution,
        *,
        outcomes: tuple[DailyMemberOutcome, ...],
        evidence: tuple[DailyOutputEvidence, ...],
        finished_at: UtcDatetime,
    ) -> DailyAttemptState: ...


def validate_daily_group(
    *,
    claim: DailyExecution,
    result: GroupForecastOutcome,
    exclusions: tuple[DailyMemberOutcome, ...],
    provenance: Mapping[StationId, DailyInputProvenance],
) -> tuple[
    dict[StationId, DailyMemberOutcome],
    dict[StationId, DailyMemberOutcome],
    dict[StationId, RejectedAssignmentPayload],
]:
    roster = {m.station_id: m for m in claim.frozen.members}
    excluded = {m.station_id: m for m in exclusions}
    if len(excluded) != len(exclusions) or any(
        m.station_id not in roster
        or m.required_parameters != roster[m.station_id].required_parameters
        or m.state
        not in (
            DailyMemberState.INPUT_EXCLUDED,
            DailyMemberState.CONFIGURATION_EXCLUDED,
        )
        for m in exclusions
    ):
        raise ValueError("daily exclusions must match the frozen roster")
    for issue in result.unavailable_members:
        if issue.station_id in excluded:
            raise ValueError("daily member has duplicate exclusions")
        if issue.station_id not in roster:
            raise ValueError("daily unavailable member is outside the frozen roster")
        reason = DailyMemberReason(issue.reason.value)
        state = (
            DailyMemberState.CONFIGURATION_EXCLUDED
            if reason is DailyMemberReason.MISSING_FORECAST_BINDING
            else DailyMemberState.INPUT_EXCLUDED
        )
        excluded[issue.station_id] = replace(
            roster[issue.station_id],
            state=state,
            reason=reason,
            provenance=provenance.get(
                issue.station_id, roster[issue.station_id].provenance
            ),
        )
    rejected = {p.station_id: p for p in result.rejected}
    output_ids = set(result.results) | set(rejected)
    if (
        len(rejected) != len(result.rejected)
        or set(result.results) & set(rejected)
        or output_ids & set(excluded)
        or not output_ids <= roster.keys()
        or not output_ids <= provenance.keys()
    ):
        raise ValueError(
            "daily outputs must be unique roster members with captured provenance"
        )
    for sid, station in result.results.items():
        if station.new_state is not None:
            raise ModelOutputError(
                "stateless daily completion received unexpected post-state"
            )
        if (
            station.station_id != sid
            or station.model_id != claim.key.model_id
            or station.artifact_id != claim.frozen.artifact_id
        ):
            raise ValueError("daily result does not match frozen identity")
        if any(
            f.station_id != sid
            or f.model_id != claim.key.model_id
            or f.model_artifact_id != claim.frozen.artifact_id
            or f.issued_at != claim.key.context.issue_time
            or f.data_use is not ForecastDataUse.STANDARD
            or f.input_lineage is not None
            for f in station.forecasts
        ):
            raise ValueError("daily forecast does not match frozen identity")
    # Validate every planned write before the first transaction begins.
    for sid, station in result.results.items():
        if not station.forecasts:
            continue
        params = tuple(
            RejectedParameterPayload(
                ensemble=f.ensemble, qc_status=f.qc_status, qc_flags=f.qc_flags
            )
            for f in station.forecasts
        )
        if _eligible_parameters(
            claim, roster[sid], params, source=DailyOutputSource.FORECAST
        ):
            validate_daily_forecasts(
                claim, sid, tuple(station.forecasts), provenance[sid]
            )
        else:
            validate_daily_rejection(
                claim,
                RejectedAssignmentPayload(
                    station_id=sid,
                    model_id=station.model_id,
                    model_artifact_id=station.artifact_id,
                    issued_at=claim.key.context.issue_time,
                    group_id=claim.key.group_id,
                    parameters=params,
                    data_use=station.forecasts[0].data_use,
                    input_lineage=station.forecasts[0].input_lineage,
                ),
                provenance[sid],
            )
    for sid, payload in rejected.items():
        validate_daily_rejection(claim, payload, provenance[sid])
        _eligible_parameters(
            claim, roster[sid], payload.parameters, source=DailyOutputSource.REJECTED
        )
    return roster, excluded, rejected


def complete_daily_group(
    *,
    claim: DailyExecution,
    result: GroupForecastOutcome,
    exclusions: tuple[DailyMemberOutcome, ...],
    provenance: Mapping[StationId, DailyInputProvenance],
    forecast_writer: CommittedForecastWriter,
    rejected_writer: CommittedRejectedForecastWriter,
    store: DailyCompletionStore,
    clock: Callable[[], UtcDatetime],
) -> CompletedDailyGroup:
    roster, excluded, rejected = validate_daily_group(
        claim=claim, result=result, exclusions=exclusions, provenance=provenance
    )
    outcomes: list[DailyMemberOutcome] = []
    all_evidence: list[DailyOutputEvidence] = []
    for sid, member in roster.items():
        if sid in excluded:
            outcomes.append(excluded[sid])
            continue
        evidence: tuple[DailyOutputEvidence, ...] = ()
        usable = False
        station = result.results.get(sid)
        payload = rejected.get(sid)
        if station is not None and station.forecasts:
            params = tuple(
                RejectedParameterPayload(
                    ensemble=f.ensemble, qc_status=f.qc_status, qc_flags=f.qc_flags
                )
                for f in station.forecasts
            )
            usable = _eligible_parameters(
                claim, member, params, source=DailyOutputSource.FORECAST
            )
            if usable:
                try:
                    evidence = commit_daily_forecasts(
                        claim,
                        sid,
                        tuple(station.forecasts),
                        provenance[sid],
                        forecast_writer,
                    )
                except DailyOutputRolledBackError as exc:
                    if all_evidence:
                        raise StoreError(
                            "earlier daily output already committed"
                        ) from exc
                    raise

            else:
                payload = RejectedAssignmentPayload(
                    station_id=sid,
                    model_id=station.model_id,
                    model_artifact_id=station.artifact_id,
                    issued_at=claim.key.context.issue_time,
                    group_id=claim.key.group_id,
                    parameters=params,
                    data_use=station.forecasts[0].data_use,
                    input_lineage=station.forecasts[0].input_lineage,
                )
        if payload is not None:
            try:
                evidence = commit_daily_rejection(
                    claim, payload, provenance[sid], rejected_writer
                )
            except DailyOutputRolledBackError as exc:
                if all_evidence:
                    raise StoreError("earlier daily output already committed") from exc
                raise
            usable = _eligible_parameters(
                claim, member, payload.parameters, source=DailyOutputSource.REJECTED
            )
        elif station is None or not station.forecasts:
            outcomes.append(
                replace(
                    member,
                    state=DailyMemberState.MODEL_FAILED,
                    reason=DailyMemberReason.MODEL_FAILURE,
                    provenance=provenance.get(sid, member.provenance),
                )
            )
            continue
        outcomes.append(
            replace(
                member,
                state=DailyMemberState.PRODUCED
                if usable
                else DailyMemberState.INTEGRITY_REJECTED,
                reason=None if usable else DailyMemberReason.OUTPUT_INTEGRITY,
                references=tuple(e.reference for e in evidence),
                provenance=provenance[sid],
            )
        )
        all_evidence.extend(evidence)
    state = store.finalize(
        claim,
        outcomes=tuple(outcomes),
        evidence=tuple(all_evidence),
        finished_at=clock(),
    )
    return CompletedDailyGroup(state=state, outcomes=tuple(outcomes))


def _eligible_parameters(
    claim: DailyExecution,
    member: DailyMemberOutcome,
    parameters: tuple[RejectedParameterPayload, ...],
    *,
    source: DailyOutputSource,
) -> bool:
    names = [p.ensemble.parameter for p in parameters]
    if set(names) != set(member.required_parameters) or len(names) != len(
        member.required_parameters
    ):
        return False
    contracts = {c.parameter: c for c in claim.frozen.output_contracts}
    allowed = {DailyOutputEligibility.USABLE}
    if source is DailyOutputSource.REJECTED:
        allowed.add(DailyOutputEligibility.PLAUSIBILITY_FLAGGED)
    for parameter in parameters:
        ensemble = parameter.ensemble
        contract = contracts.get(ensemble.parameter)
        key = ForecastOutputKey(
            station_id=member.station_id,
            model_id=claim.key.model_id,
            issued_at=claim.key.context.issue_time,
            parameter=ensemble.parameter,
        )
        if (
            contract is None
            or assess_daily_output(
                ensemble, key, contract, parameter.qc_status, parameter.qc_flags
            )
            not in allowed
        ):
            return False
    return True
