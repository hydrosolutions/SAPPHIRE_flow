# ruff: noqa: F811 -- Reuse the selector scenario fixture.
from dataclasses import replace
from types import SimpleNamespace

import polars as pl

from sapphire_flow.services.daily_forecast_completion import complete_daily_group
from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import DailyClaim
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberState,
    finalized_outcome_state,
)
from sapphire_flow.types.daily_forecast_snapshot import DailyAttemptSnapshot
from sapphire_flow.types.daily_output import DailyOutputEvidence
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import RejectedForecastId
from sapphire_flow.types.rejected_forecast import (
    PersistedRejectedForecast,
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.unit.services.test_evaluation_forecasts import case, read  # noqa: F401


def test_complete_plausibility_output_still_requires_read_allowlist(
    case: SimpleNamespace,
) -> None:
    ensemble = replace(
        case.record.ensemble,
        values=case.record.ensemble.values.with_columns(pl.lit(-1.0).alias("value")),
    )
    flag = QcFlag(
        rule_id="negative_value",
        rule_version="1",
        status=QcStatus.QC_FAILED,
        detail="negative",
    )
    payload = RejectedAssignmentPayload(
        station_id=case.station.id,
        model_id=case.record.model_id,
        model_artifact_id=case.record.model_artifact_id,
        issued_at=case.issue,
        group_id=case.settings.group_ids[0],
        parameters=(
            RejectedParameterPayload(
                ensemble=ensemble, qc_status=QcStatus.QC_FAILED, qc_flags=(flag,)
            ),
        ),
    )

    class Writer:
        def write_committed(
            self, entry: RejectedForecastEntry
        ) -> dict[ForecastOutputKey, RejectedForecastId]:
            assert entry.payload == payload
            record = PersistedRejectedForecast(
                id=case.record.id,
                attempt_id=entry.attempt_id,
                recorded_at=case.issue,
                station_id=payload.station_id,
                model_id=payload.model_id,
                model_artifact_id=payload.model_artifact_id,
                group_id=payload.group_id,
                issued_at=payload.issued_at,
                parameter="discharge",
                representation=ensemble.representation,
                units=ensemble.units,
                time_step_seconds=3600,
                qc_status=QcStatus.QC_FAILED,
                qc_flags=(flag,),
                values={
                    str(k): tuple(
                        (r["valid_time"], r["value"])
                        for r in ensemble.values.to_dicts()
                        if r["member_id"] == k
                    )
                    for k in (0, 1)
                },
            )
            case.stores.rejected.records = [record]
            return {
                ForecastOutputKey(
                    station_id=payload.station_id,
                    model_id=payload.model_id,
                    issued_at=payload.issued_at,
                    parameter="discharge",
                ): record.id
            }

    class Finalizer:
        def finalize(
            self,
            claim: DailyClaim,
            *,
            outcomes: tuple[DailyMemberOutcome, ...],
            evidence: tuple[DailyOutputEvidence, ...],
            finished_at: UtcDatetime,
        ) -> DailyAttemptState:
            assert evidence[0].reference.output_id == case.stores.rejected.records[0].id
            state = finalized_outcome_state(outcomes)
            summary = DailyAttemptSnapshot(
                claim=claim,
                state=state,
                outcomes=outcomes,
                finished_at=finished_at,
                failure_reason=None,
                recovery=None,
            )
            case.days.day = replace(
                case.days.day, latest=summary, authoritative=summary
            )
            return state

    case.records.records = []
    completed = complete_daily_group(
        claim=case.summary.claim,
        result=GroupForecastOutcome(results={}, rejected=(payload,)),
        exclusions=(),
        provenance={case.station.id: case.summary.outcomes[0].provenance},
        forecast_writer=SimpleNamespace(),
        rejected_writer=Writer(),
        store=Finalizer(),
        clock=lambda: case.issue,
    )
    assert completed.state is DailyAttemptState.COMPLETE
    assert completed.outcomes[0].state is DailyMemberState.PRODUCED
    unavailable = read(case)
    assert unavailable.parameters[0].candidate is None
    assert unavailable.daily[0].references[0].usability.value == "unavailable"
    enabled = read(
        case,
        settings=replace(
            case.settings, plausibility_models=frozenset({case.record.model_id})
        ),
    )
    assert enabled.parameters[0].candidate.record.qc_status is QcStatus.QC_FAILED
