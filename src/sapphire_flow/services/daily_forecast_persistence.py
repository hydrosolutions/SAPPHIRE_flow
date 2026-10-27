from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.exceptions import StoreError
from sapphire_flow.services.daily_output_integrity import assess_daily_output
from sapphire_flow.types.daily_forecast_outcome import (
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_output import DailyOutputEligibility, DailyOutputEvidence
from sapphire_flow.types.enums import ForecastDataUse, ForecastStatus
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.rejected_forecast import RejectedForecastEntry

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from sapphire_flow.protocols.stores import (
        CommittedForecastWriter,
        CommittedRejectedForecastWriter,
    )
    from sapphire_flow.types.daily_forecast_one_shot import DailyExecution
    from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.rejected_forecast import RejectedAssignmentPayload


def _required_keys(
    claim: DailyExecution, station_id: StationId
) -> set[ForecastOutputKey]:
    if claim.frozen.artifact_id is None:
        raise ValueError("daily output requires a frozen artifact")
    member = next((m for m in claim.frozen.members if m.station_id == station_id), None)
    if member is None:
        raise ValueError("daily output station is outside the frozen roster")
    return {
        ForecastOutputKey(
            station_id=station_id,
            model_id=claim.key.model_id,
            issued_at=claim.key.context.issue_time,
            parameter=p,
        )
        for p in member.required_parameters
    }


def _verify_returned_keys(
    expected: tuple[ForecastOutputKey, ...], mapping: Mapping[ForecastOutputKey, UUID]
) -> None:
    if set(mapping) != set(expected) or len(set(mapping.values())) != len(expected):
        raise StoreError("committed daily output mapping differs from requested keys")


def validate_daily_forecasts(
    claim: DailyExecution,
    station_id: StationId,
    forecasts: tuple[OperationalForecast, ...],
    provenance: DailyInputProvenance,
) -> tuple[ForecastOutputKey, ...]:
    required = _required_keys(claim, station_id)
    keys = tuple(
        ForecastOutputKey(
            station_id=f.station_id,
            model_id=f.model_id,
            issued_at=f.issued_at,
            parameter=f.ensemble.parameter,
        )
        for f in forecasts
    )
    if set(keys) != required or len(keys) != len(required):
        raise ValueError("daily forecasts must match every frozen member parameter")
    contracts = {c.parameter: c for c in claim.frozen.output_contracts}
    for forecast, key in zip(forecasts, keys, strict=True):
        if (
            forecast.model_artifact_id != claim.frozen.artifact_id
            or forecast.data_use is not ForecastDataUse.STANDARD
            or forecast.status is ForecastStatus.SUPERSEDED
            or forecast.representation is not forecast.ensemble.representation
        ):
            raise ValueError("daily forecast does not match frozen artifact or scope")
        contract = contracts.get(key.parameter)
        if (
            contract is None
            or assess_daily_output(
                forecast.ensemble, key, contract, forecast.qc_status, forecast.qc_flags
            )
            is not DailyOutputEligibility.USABLE
        ):
            raise ValueError(
                "daily forecast requires rejected evidence, not ordinary storage"
            )
        if (
            forecast.nwp_cycle_source != provenance.nwp_source
            or forecast.nwp_cycle_reference_time != provenance.nwp_reference_time
            or forecast.observation_staleness_hours
            != provenance.observation_staleness_hours
            or forecast.input_quality != provenance.input_quality
            or forecast.input_quality_flags != provenance.input_quality_flags
        ):
            raise ValueError("daily forecast provenance differs from captured inputs")
    return keys


def commit_daily_forecasts(
    claim: DailyExecution,
    station_id: StationId,
    forecasts: tuple[OperationalForecast, ...],
    provenance: DailyInputProvenance,
    writer: CommittedForecastWriter,
) -> tuple[DailyOutputEvidence, ...]:
    keys = validate_daily_forecasts(claim, station_id, forecasts, provenance)
    mapping = writer.write_committed(forecasts)
    _verify_returned_keys(keys, mapping)
    return tuple(
        DailyOutputEvidence(
            reference=DailyOutputReference(
                key=key, source=DailyOutputSource.FORECAST, output_id=mapping[key]
            ),
            ensemble=forecast.ensemble,
            qc_status=forecast.qc_status,
            qc_flags=forecast.qc_flags,
            provenance=provenance,
        )
        for forecast, key in zip(forecasts, keys, strict=True)
    )


def validate_daily_rejection(
    claim: DailyExecution,
    payload: RejectedAssignmentPayload,
    provenance: DailyInputProvenance,
) -> tuple[ForecastOutputKey, ...]:
    _required_keys(claim, payload.station_id)
    if (
        payload.model_id != claim.key.model_id
        or payload.model_artifact_id != claim.frozen.artifact_id
        or payload.issued_at != claim.key.context.issue_time
        or payload.group_id != claim.key.group_id
        or payload.data_use is not ForecastDataUse.STANDARD
        or payload.input_lineage is not None
    ):
        raise ValueError("daily rejection does not match frozen artifact or scope")
    keys = tuple(
        ForecastOutputKey(
            station_id=payload.station_id,
            model_id=payload.model_id,
            issued_at=payload.issued_at,
            parameter=p.ensemble.parameter,
        )
        for p in payload.parameters
    )
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("daily rejection requires nonempty unique parameter evidence")
    return keys


def commit_daily_rejection(
    claim: DailyExecution,
    payload: RejectedAssignmentPayload,
    provenance: DailyInputProvenance,
    writer: CommittedRejectedForecastWriter,
) -> tuple[DailyOutputEvidence, ...]:
    keys = validate_daily_rejection(claim, payload, provenance)
    mapping = writer.write_committed(
        RejectedForecastEntry(attempt_id=claim.id, payload=payload)
    )
    _verify_returned_keys(keys, mapping)
    return tuple(
        DailyOutputEvidence(
            reference=DailyOutputReference(
                key=key, source=DailyOutputSource.REJECTED, output_id=mapping[key]
            ),
            ensemble=parameter.ensemble,
            qc_status=parameter.qc_status,
            qc_flags=parameter.qc_flags,
            provenance=provenance,
        )
        for parameter, key in zip(payload.parameters, keys, strict=True)
    )
