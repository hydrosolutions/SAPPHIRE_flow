from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import forecasts, rejected_forecasts
from sapphire_flow.services.daily_output_integrity import assess_daily_output
from sapphire_flow.store.forecast_read import forecast_columns
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import (
    encode_rejected_qc_flags,
    encode_rejected_series,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberState,
    DailyOutputSource,
)
from sapphire_flow.types.daily_output import DailyOutputEligibility
from sapphire_flow.types.enums import ForecastStatus, QcStatus
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from sapphire_flow.types.daily_forecast_one_shot import DailyExecution
    from sapphire_flow.types.daily_output import DailyOutputEvidence


def verify_daily_output(
    txn: sa.Connection,
    claim: DailyExecution,
    evidence: DailyOutputEvidence,
    state: DailyMemberState,
) -> None:
    ref, ensemble = evidence.reference, evidence.ensemble
    if (
        ref.key.model_id != claim.key.model_id
        or ref.key.issued_at != claim.key.context.issue_time
    ):
        raise ValueError("daily output reference has the wrong attempt identity")
    table = (
        forecasts if ref.source is DailyOutputSource.FORECAST else rejected_forecasts
    )
    # Protect ordinary supersession until this finalization commits. A later
    # supersession must still be checked by the evaluation reader.
    query = sa.select(*forecast_columns(table, claim.key.data_use)).where(
        table.c.id == ref.output_id
    )
    if ref.source is DailyOutputSource.FORECAST:
        query = query.with_for_update(read=True)
    # Rejected evidence is append-only; locking it would require forbidden UPDATE.
    row = txn.execute(query).mappings().one_or_none()
    if row is None:
        raise ValueError("daily output reference is not committed")
    expected_identity = (
        ref.key.station_id,
        ref.key.model_id,
        ref.key.issued_at,
        ref.key.parameter,
        claim.frozen.artifact_id,
        claim.key.data_use.value,
    )
    actual_identity = (
        row["station_id"],
        row["model_id"],
        row["issued_at"],
        row["parameter"],
        row["model_artifact_id"],
        row["data_use"],
    )
    if actual_identity != expected_identity:
        raise ValueError("daily output reference does not match committed identity")
    if row["qc_status"] != evidence.qc_status.value or row[
        "qc_flags"
    ] != encode_rejected_qc_flags(evidence.qc_flags):
        raise ValueError("daily output QC does not match committed evidence")
    if ref.source is DailyOutputSource.REJECTED:
        if row["attempt_id"] != claim.id or row["group_id"] != claim.key.group_id:
            raise ValueError("daily rejected evidence belongs to another attempt")
        matching = (
            row["values"] == encode_rejected_series(ensemble)
            and row["units"] == ensemble.units
            and row["representation"] == ensemble.representation.value
            and row["time_step_seconds"] == ensemble.time_step.total_seconds()
        )
    else:
        stored = PgForecastStore(txn).fetch_forecast(ForecastId(ref.output_id))
        if stored is None or stored.status is ForecastStatus.SUPERSEDED:
            raise ValueError("daily ordinary output is absent or superseded")
        provenance = evidence.provenance
        if (
            stored.nwp_cycle_source != provenance.nwp_source
            or stored.nwp_cycle_reference_time != provenance.nwp_reference_time
            or stored.observation_staleness_hours
            != provenance.observation_staleness_hours
            or stored.input_quality != provenance.input_quality
            or stored.input_quality_flags != provenance.input_quality_flags
        ):
            raise ValueError(
                "daily input provenance does not match the ordinary record"
            )
        matching = (
            encode_rejected_series(stored.ensemble) == encode_rejected_series(ensemble)
            and stored.ensemble.units == ensemble.units
            and stored.ensemble.representation is ensemble.representation
            and stored.ensemble.time_step == ensemble.time_step
            and stored.ensemble.forecast_horizon_steps
            == ensemble.forecast_horizon_steps
        )
        if evidence.qc_status not in (QcStatus.QC_PASSED, QcStatus.QC_SUSPECT):
            raise ValueError(
                "daily ordinary output must retain ordinary QC eligibility"
            )
    if not matching:
        raise ValueError(
            "daily committed values or metadata differ from supplied output"
        )
    if state is not DailyMemberState.PRODUCED:
        return
    contract = next(
        (c for c in claim.frozen.output_contracts if c.parameter == ref.key.parameter),
        None,
    )
    if contract is None:
        raise ValueError("daily produced output has no frozen declared contract")
    eligibility = assess_daily_output(
        ensemble, ref.key, contract, evidence.qc_status, evidence.qc_flags
    )
    if eligibility not in (
        DailyOutputEligibility.USABLE,
        DailyOutputEligibility.PLAUSIBILITY_FLAGGED,
    ):
        raise ValueError("daily produced output failed integrity or QC assessment")
