"""Plan 402 — the two REVIEW-gated routes the flow map reads: T1's
`GET /api/v1/qc/rules[?station_id=]` and T2's
`GET /api/v1/stations/{id}/skill`. Both require a reviewer or admin token
(`Depends(require_reviewer)`, Plan 401) and, when they serve station data,
the principal's station scope (`ensure_station_in_scope`) exactly like every
other station-scoped route.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.schemas import (
    ForecastQcRuleResponse,
    ForecastQcRuleSetResponse,
    ObservationQcRuleResponse,
    ObservationQcRuleSetResponse,
    QcRulesResponse,
    QcRulesUnion,
    SkillRowResponse,
    StationObservationQcRuleResponse,
    StationObservationQcRuleSetResponse,
    StationQcRulesResponse,
    StationSkillResponse,
)
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    require_reviewer,
)
from sapphire_flow.config.forecast_qc_rules import resolve_forecast_qc_rules
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.services._qc_helpers import merge_thresholds
from sapphire_flow.services.forecast_qc import FORECAST_QC_SEVERITY
from sapphire_flow.services.qc import OBSERVATION_QC_SEVERITY
from sapphire_flow.services.qc_datum import obs_skipped_rules
from sapphire_flow.services.skill_read import (
    fetch_station_skill_rows,
    forecast_lab_stores_from_dict,
)
from sapphire_flow.services.station_qc_overrides import (
    is_ingest_station_judged,
    resolve_configured_station_qc,
)
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.ids import ModelId, StationId

if TYPE_CHECKING:
    from sapphire_flow.types.domain import (
        ForecastQcRuleParams,
        ForecastQcRuleSet,
        QcRuleParams,
        QcRuleSet,
        StationQcOverride,
    )
    from sapphire_flow.types.station import StationConfig

router = APIRouter(prefix="/api/v1", tags=["api-review"])


def _parse_station_id(value: str) -> StationId:
    """Plan 402 T1/T2: 400 on a malformed id — unlike the existing
    `/stations/{id}` family (`api/routes/api_stations.py`), which still
    raises an unguarded `StationId(UUID(value))` -> 500 (noted on the
    consumer page, not changed here)."""
    try:
        return StationId(UUID(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid station_id: {value}"
        ) from exc


def _serve_thresholds(thresholds: dict[str, float]) -> dict[str, float | None]:
    return {
        name: (value if math.isfinite(value) else None)
        for name, value in thresholds.items()
    }


def _to_observation_rule_response(rule: QcRuleParams) -> ObservationQcRuleResponse:
    return ObservationQcRuleResponse(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        parameter=rule.parameter,
        time_step_seconds=int(rule.time_step.total_seconds()),
        network=rule.network,
        severity=OBSERVATION_QC_SEVERITY[rule.rule_id].value,  # type: ignore[arg-type]
        thresholds=_serve_thresholds(rule.thresholds),
    )


def _to_forecast_rule_response(rule: ForecastQcRuleParams) -> ForecastQcRuleResponse:
    return ForecastQcRuleResponse(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        parameter=rule.parameter,
        time_step_seconds=int(rule.time_step.total_seconds()),
        severity=FORECAST_QC_SEVERITY[rule.rule_id].value,  # type: ignore[arg-type]
        thresholds=_serve_thresholds(rule.thresholds),
    )


def _build_observation_rule_set(
    rule_set: QcRuleSet, source: str
) -> ObservationQcRuleSetResponse:
    return ObservationQcRuleSetResponse(
        version=rule_set.version,
        source=source,  # type: ignore[arg-type]
        rules=[_to_observation_rule_response(r) for r in rule_set.rules],
    )


def _build_forecast_rule_set(
    rule_set: ForecastQcRuleSet, source: str
) -> ForecastQcRuleSetResponse:
    return ForecastQcRuleSetResponse(
        version=rule_set.version,
        source=source,  # type: ignore[arg-type]
        rules=[_to_forecast_rule_response(r) for r in rule_set.rules],
    )


def _station_selected_rules(rule_set: QcRuleSet, *, network: str) -> list[QcRuleParams]:
    """The rules `QcRuleSet.rules_for` selects for EVERY distinct
    `(parameter, time_step)` pair the set declares, listed in the set's own
    config order (D15) — reuses `rules_for`, never a second copy of the
    replacement logic."""
    order_index = {id(rule): i for i, rule in enumerate(rule_set.rules)}
    pairs = dict.fromkeys((r.parameter, r.time_step) for r in rule_set.rules)
    selected: list[QcRuleParams] = []
    for parameter, time_step in pairs:
        selected.extend(rule_set.rules_for(parameter, time_step, network=network))
    selected.sort(key=lambda r: order_index[id(r)])
    return selected


def _build_station_observation_rule_set(
    rule_set: QcRuleSet,
    source: str,
    *,
    station: StationConfig,
    overrides: list[StationQcOverride],
) -> StationObservationQcRuleSetResponse:
    rows: list[StationObservationQcRuleResponse] = []
    for rule in _station_selected_rules(rule_set, network=station.network):
        merged = merge_thresholds(
            rule.thresholds,
            overrides,
            station.id,
            rule.rule_id,
            rule.parameter,
            rule.time_step,
        )
        station_names = sorted(
            {
                name
                for o in overrides
                if o.station_id == station.id
                and o.rule_id == rule.rule_id
                and o.parameter == rule.parameter
                and o.time_step == rule.time_step
                for name, value in o.thresholds.items()
                if value is not None
            }
        )
        skipped = (
            "no_datum"
            if rule.rule_id
            in obs_skipped_rules(rule.parameter, station.water_level_datum_masl)
            else None
        )
        rows.append(
            StationObservationQcRuleResponse(
                rule_id=rule.rule_id,
                rule_version=rule.rule_version,
                parameter=rule.parameter,
                time_step_seconds=int(rule.time_step.total_seconds()),
                network=rule.network,
                severity=OBSERVATION_QC_SEVERITY[rule.rule_id].value,  # type: ignore[arg-type]
                thresholds=_serve_thresholds(merged),
                station_thresholds=station_names,
                skipped=skipped,  # type: ignore[arg-type]
            )
        )
    return StationObservationQcRuleSetResponse(
        version=rule_set.version,
        source=source,  # type: ignore[arg-type]
        rules=rows,
    )


@router.get("/qc/rules", response_model=QcRulesUnion)
def get_qc_rules(
    station_id: str | None = Query(None),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_reviewer),
) -> QcRulesResponse | StationQcRulesResponse:
    observation_rule_set, observation_source = resolve_qc_rules()
    forecast_rule_set, forecast_source = resolve_forecast_qc_rules()

    if station_id is None:
        return QcRulesResponse(
            observation=_build_observation_rule_set(
                observation_rule_set, observation_source
            ),
            forecast=_build_forecast_rule_set(forecast_rule_set, forecast_source),
        )

    sid = _parse_station_id(station_id)
    ensure_station_in_scope(principal, sid)
    station = stores["station_store"].fetch_station(sid)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    all_stations = stores["station_store"].fetch_all_stations()
    outcome = resolve_configured_station_qc(
        all_stations, observation_rule_set, stores["tenant_store"]
    )
    station_overrides = [o for o in outcome.overrides if o.station_id == sid]

    return StationQcRulesResponse(
        station_id=str(sid),
        station_qc="judged" if is_ingest_station_judged(station) else "not_judged",
        water_level_datum_masl=station.water_level_datum_masl,
        observation=_build_station_observation_rule_set(
            observation_rule_set,
            observation_source,
            station=station,
            overrides=station_overrides,
        ),
        forecast=_build_forecast_rule_set(forecast_rule_set, forecast_source),
    )


@router.get("/stations/{station_id}/skill", response_model=StationSkillResponse)
def get_station_skill(
    station_id: str,
    model_id: str | None = Query(None),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_reviewer),
) -> StationSkillResponse:
    sid = _parse_station_id(station_id)
    ensure_station_in_scope(principal, sid)
    station = stores["station_store"].fetch_station(sid)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    now = UtcDatetime(datetime.now(UTC))
    rows = fetch_station_skill_rows(
        forecast_lab_stores_from_dict(stores),
        stores["artifact_store"],
        stores["skill_store"],
        station_id=sid,
        now=now,
        model_id=ModelId(model_id) if model_id is not None else None,
    )
    return StationSkillResponse(
        station_id=str(sid),
        rows=[
            SkillRowResponse(
                model_id=str(r.model_id),
                model_artifact_id=str(r.model_artifact_id),
                generation_id=str(r.generation_id) if r.generation_id else None,
                skill_source=r.skill_source.value,  # type: ignore[arg-type]
                forcing_type=r.forcing_type.value if r.forcing_type else None,  # type: ignore[arg-type]
                computation_version=r.computation_version,
                time_step_seconds=r.time_step_seconds,
                phase_offset_seconds=r.phase_offset_seconds,
                eval_period_start=r.eval_period_start,
                eval_period_end=r.eval_period_end,
                training_period_start=r.training_period_start,
                training_period_end=r.training_period_end,
                evaluated_on=r.evaluated_on,
                lead_time_hours=r.lead_time_hours,
                season=r.season,
                flow_regime=r.flow_regime.value if r.flow_regime else None,  # type: ignore[arg-type]
                metric=r.metric,
                score=r.score if math.isfinite(r.score) else None,
                sample_size=r.sample_size,
            )
            for r in rows
        ],
    )
