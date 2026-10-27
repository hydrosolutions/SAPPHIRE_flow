# ruff: noqa: TC001 -- FastAPI/Pydantic resolve annotations at runtime.
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from sapphire_flow.api.deps import (
    get_daily_evaluation_reader,
    get_deployment_config,
    get_evaluation_clock,
    get_evaluation_contracts,
    get_stores,
)
from sapphire_flow.api.evaluation_schemas import (
    EvaluationAssignmentResponse,
    EvaluationDailyResponse,
    EvaluationForecastsResponse,
    EvaluationInputFlag,
    EvaluationModelDiagnosticResponse,
    EvaluationParameterResponse,
    EvaluationProvenance,
    EvaluationReferenceResponse,
    EvaluationSelected,
    EvaluationValue,
)
from sapphire_flow.api.forecast_read import require_standard_store
from sapphire_flow.api.human_auth import require_human_station_permission
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.review_auth import require_reviewer_or_human
from sapphire_flow.api.routes.api_review import (
    _parse_station_id,  # pyright: ignore[reportPrivateUsage]
)
from sapphire_flow.api.schemas import QcFlagResponse
from sapphire_flow.api.security import Principal, ensure_station_in_scope
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.services.evaluation_forecasts import (
    DailyEvaluationReader,
    EvaluationStores,
    read_evaluation_forecasts,
    resolve_evaluation_assignments,
)
from sapphire_flow.services.forecast_read import ForecastReadUnavailableError
from sapphire_flow.types.daily_output import DailyOutputContract
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.evaluation_forecast import EvaluationCandidate
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.human_auth import HumanPermission, HumanPrincipal
from sapphire_flow.types.ids import ModelId

router = APIRouter(prefix="/api/v1", tags=["api-evaluation-forecasts"])


def _selected(candidate: EvaluationCandidate, *, withheld: bool) -> EvaluationSelected:
    row = candidate.record
    values: dict[str, list[EvaluationValue]] | None = None
    if not withheld:
        values = {}
        if isinstance(row, OperationalForecast):
            frame = row.ensemble.values
            key = "member_id" if "member_id" in frame.columns else "quantile"
            for point in frame.iter_rows(named=True):
                values.setdefault(str(point[key]), []).append(
                    EvaluationValue(
                        valid_time=point["valid_time"], value=point["value"]
                    )
                )
        else:
            values = {
                key: [EvaluationValue(valid_time=t, value=v) for t, v in points]
                for key, points in row.values.items()
            }
    return EvaluationSelected(
        output_id=str(row.id),
        parameter=candidate.reference.key.parameter,
        source=candidate.reference.source.value,
        model_id=str(row.model_id),
        issued_at=row.issued_at,
        age_seconds=candidate.age.total_seconds(),
        representation=row.representation,
        units=row.ensemble.units if isinstance(row, OperationalForecast) else row.units,
        time_step_seconds=(
            row.ensemble.time_step.total_seconds()
            if isinstance(row, OperationalForecast)
            else row.time_step_seconds
        ),
        qc_status=row.qc_status,
        qc_flags=[
            QcFlagResponse(
                rule_id=f.rule_id,
                rule_version=f.rule_version,
                status=f.status.value,  # type: ignore[arg-type]
                detail=None if withheld else f.detail,
            )
            for f in row.qc_flags
        ],
        input_quality=row.input_quality
        if isinstance(row, OperationalForecast) and row.input_quality
        else None,
        input_quality_flags=[
            EvaluationInputFlag(
                category=f.category,
                level=f.level,
                detail=None if withheld else f.detail,
            )
            for f in row.input_quality_flags
        ]
        if isinstance(row, OperationalForecast)
        else [],
        provenance=EvaluationProvenance(
            nwp_source=row.nwp_cycle_source,
            nwp_reference_time=row.nwp_cycle_reference_time,
            observation_staleness_hours=row.observation_staleness_hours,
        )
        if isinstance(row, OperationalForecast) and not withheld
        else None,
        values=values,
        withheld=withheld,
    )


@router.get(
    "/stations/{station_id}/evaluation-forecasts",
    response_model=EvaluationForecastsResponse,
)
def get_evaluation_forecasts(
    station_id: str,
    valid_at: str | None = None,
    principal: Principal | HumanPrincipal = Depends(require_reviewer_or_human),
    stores: dict[str, Any] = Depends(get_stores),
    config: DeploymentConfig | None = Depends(get_deployment_config),
    daily: DailyEvaluationReader = Depends(get_daily_evaluation_reader),
    contracts: Mapping[ModelId, tuple[DailyOutputContract, ...]] = Depends(
        get_evaluation_contracts
    ),
    as_of: datetime = Depends(get_evaluation_clock),
    gate: PublicationGate = Depends(get_publication_gate),
) -> EvaluationForecastsResponse:
    sid = _parse_station_id(station_id)
    if isinstance(principal, Principal):
        ensure_station_in_scope(principal, sid)
    else:
        require_human_station_permission(principal, sid, HumanPermission.REVIEW)
    station = stores["station_store"].fetch_station(sid)
    if station is None:
        raise HTTPException(404, "Station not found")
    if (
        not (
            isinstance(principal, Principal) and principal.role is AccessTokenRole.ADMIN
        )
        and principal.tenant_id != station.tenant_id
    ):
        raise HTTPException(404, "Station not found")
    if config is None or config.evaluation_forecasts is None:
        raise HTTPException(404, "Evaluation forecasts are unavailable")
    settings = config.evaluation_forecasts.to_domain()
    if station.tenant_id != settings.tenant_id:
        raise HTTPException(404, "Evaluation forecasts are unavailable")
    requested = None
    if valid_at is not None:
        try:
            requested = datetime.fromisoformat(valid_at)
            if requested.utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError:
            raise HTTPException(400, "valid_at must be an aware ISO datetime") from None
    require_standard_store(stores["forecast_store"])
    require_standard_store(stores["rejected_forecast_store"])
    try:
        assignments = resolve_evaluation_assignments(
            station=station,
            settings=settings,
            station_store=stores["station_store"],
            group_store=stores["group_store"],
            contracts=contracts,
        )
        result = read_evaluation_forecasts(
            station=station,
            settings=settings,
            assignments=assignments,
            stores=EvaluationStores(
                daily=daily,
                forecasts=stores["forecast_store"],
                rejected=stores["rejected_forecast_store"],
                groups=stores["group_store"],
            ),
            as_of=as_of,
            valid_at=requested,
        )
    except (ValueError, ForecastReadUnavailableError):
        raise HTTPException(503, "Evaluation forecasts are unavailable") from None
    withheld = (
        isinstance(principal, Principal)
        and principal.role is AccessTokenRole.REVIEWER
        and gate.active(station.tenant_id)
    )
    return EvaluationForecastsResponse(
        station_id=str(sid),
        as_of=result.as_of,
        valid_at=result.valid_at,
        issue_time=result.issue_time,
        diagnostic_window_start=result.diagnostic_window_start,
        parameters=[
            EvaluationParameterResponse(
                parameter=p.parameter,
                selection=p.selection.value,
                absence=p.absence.value if p.absence else None,
                daily_absence=p.daily_absence.value if p.daily_absence else None,
                selected=_selected(p.candidate, withheld=withheld)
                if p.candidate
                else None,
            )
            for p in result.parameters
        ],
        assignments=[
            EvaluationAssignmentResponse(
                model_id=a.model_id,
                priority=a.priority,
                declared_parameters=[c.parameter for c in a.contracts],
            )
            for a in result.assignments
        ],
        models=[
            EvaluationModelDiagnosticResponse(
                model_id=d.model_id,
                availability=d.availability.value,
                freshness=d.freshness.value,
                latest_result_at=d.latest_result_at,
                age_seconds=d.age.total_seconds() if d.age is not None else None,
                diagnostic_window_start=d.diagnostic_window_start,
                records=[_selected(c, withheld=withheld) for c in d.records],
                evidence_authority=d.evidence_authority.value,
                stored_result=EvaluationReferenceResponse(
                    usability=None if withheld else "unverified",
                    output_id=str(d.stored_result.output_id),
                    source=d.stored_result.source.value,
                    model_id=d.stored_result.key.model_id,
                    parameter=d.stored_result.key.parameter,
                    issued_at=d.stored_result.key.issued_at,
                )
                if d.stored_result
                else None,
                group_id=str(d.group_id) if d.group_id else None,
                issue_time=d.issue_time,
                latest_attempt_id=str(d.latest_attempt_id)
                if d.latest_attempt_id
                else None,
                latest_state=d.latest_state,
                authoritative_attempt_id=str(d.authoritative_attempt_id)
                if d.authoritative_attempt_id
                else None,
            )
            for d in result.models
        ],
        daily=[
            EvaluationDailyResponse(
                group_id=str(d.group_id),
                issue_time=d.issue_time,
                latest_attempt_id=str(d.latest_attempt_id)
                if d.latest_attempt_id
                else None,
                availability=d.availability.value,
                latest_state=d.latest_state
                if d.latest_state
                else (
                    "unavailable"
                    if d.availability.value == "post_as_of"
                    else "not_attempted"
                ),
                failure_reason=d.failure_reason if d.failure_reason else None,
                authoritative_attempt_id=str(d.authoritative_attempt_id)
                if d.authoritative_attempt_id
                else None,
                member_state=d.member.state if d.member else None,
                member_reason=d.member.reason if d.member and d.member.reason else None,
                required_parameters=list(d.member.required_parameters)
                if d.member
                else [],
                references=[
                    EvaluationReferenceResponse(
                        output_id=str(item.reference.output_id),
                        source=item.reference.source.value,
                        model_id=item.reference.key.model_id,
                        parameter=item.reference.key.parameter,
                        issued_at=item.reference.key.issued_at,
                        usability=None if withheld else item.usability.value,
                    )
                    for item in d.references
                ]
                if d.member
                else [],
                recovery_status=d.recovery_status,
                provenance=EvaluationProvenance.model_validate(
                    jsonable_encoder(d.member.provenance)
                )
                if d.member and not withheld
                else None,
                withheld=withheld,
            )
            for d in result.daily
        ],
    )
