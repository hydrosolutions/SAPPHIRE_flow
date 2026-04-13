from __future__ import annotations

import random  # noqa: TC003
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

import structlog

from sapphire_flow.services.input_quality import assess_input_quality
from sapphire_flow.services.operational_inputs import (
    OperationalInputMetadata,  # noqa: TC001
)
from sapphire_flow.types.enums import ForecastStatus, QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import ModelArtifactStore
    from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import (
        ClimBaseline,
        ForecastQcRuleSet,
        StationForecastQcOverride,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.model import StationModelInputs
    from sapphire_flow.types.station import ModelAssignment

log = structlog.get_logger()


@dataclass(frozen=True, kw_only=True, slots=True)
class StationForecastResult:
    station_id: StationId
    model_id: ModelId
    artifact_id: ArtifactId
    forecasts: list[OperationalForecast]
    new_state: bytes | None
    ensembles: dict[str, ForecastEnsemble]


def _worst_qc_status(flags: list) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED
    priority = {
        QcStatus.QC_FAILED: 3,
        QcStatus.QC_SUSPECT: 2,
        QcStatus.QC_PASSED: 1,
        QcStatus.RAW: 0,
        QcStatus.MISSING: 0,
    }
    return max(flags, key=lambda f: priority.get(f.status, 0)).status


def run_station_forecast(
    station_id: StationId,
    inputs: StationModelInputs,
    input_metadata: OperationalInputMetadata,
    assignments: list[ModelAssignment],
    models: dict[ModelId, ForecastModel],
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    nwp_cycle_reference_time: UtcDatetime,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
) -> StationForecastResult | None:
    sorted_assignments = sorted(assignments, key=lambda a: a.priority)

    for assignment in sorted_assignments:
        model = models.get(assignment.model_id)
        if model is None:
            log.warning(
                "run_station_forecast.model_not_found",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
            )
            continue

        artifact_result = artifact_store.fetch_active_artifact_for_station(
            station_id, assignment.model_id
        )
        if artifact_result is None:
            log.warning(
                "run_station_forecast.no_active_artifact",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
            )
            continue

        artifact_id, artifact_bytes = artifact_result

        try:
            ensembles, new_state = model.predict(  # type: ignore[union-attr]
                artifact_bytes,
                inputs,
                rng,
                prior_state=input_metadata.prior_state,
            )
        except Exception as exc:
            log.warning(
                "run_station_forecast.predict_failed",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                error=str(exc),
            )
            continue

        all_flags: dict[str, list] = {}
        qc_failed = False
        for param, ensemble in ensembles.items():
            flags = qc_checker.check(ensemble, qc_rules, qc_overrides, baselines)
            all_flags[param] = flags
            worst = _worst_qc_status(flags)
            if worst == QcStatus.QC_FAILED:
                log.warning(
                    "run_station_forecast.qc_failed",
                    station_id=str(station_id),
                    model_id=str(assignment.model_id),
                    parameter=param,
                )
                qc_failed = True
                break

        if qc_failed:
            continue

        iq_config = config.input_quality
        input_quality, input_quality_flags = assess_input_quality(
            observation_staleness_hours=input_metadata.observation_staleness_hours,
            warm_up_source=input_metadata.warm_up_source,  # type: ignore[arg-type]
            warm_up_state_age_hours=input_metadata.warm_up_state_age_hours,
            nwp_cycle_source=nwp_cycle_source,
            nwp_age_hours=input_metadata.nwp_age_hours,
            obs_partial_hours=config.observation_staleness_warning_hours,
            config=iq_config,
            warmup_partial_hours=iq_config.warmup_snapshot_age_partial_hours,
            warmup_degraded_hours=iq_config.warmup_snapshot_age_degraded_hours,
        )

        forecasts: list[OperationalForecast] = []
        now = clock()
        for param, ensemble in ensembles.items():
            flags = all_flags[param]
            qc_status = _worst_qc_status(flags)
            forecast = OperationalForecast(
                id=ForecastId(id_gen()),
                station_id=station_id,
                model_id=assignment.model_id,
                model_artifact_id=artifact_id,
                issued_at=inputs.issue_time,
                nwp_cycle_reference_time=nwp_cycle_reference_time,
                nwp_cycle_source=nwp_cycle_source,
                representation=ensemble.representation,
                status=ForecastStatus.RAW,
                version=1,
                warm_up_source=input_metadata.warm_up_source,  # type: ignore[arg-type]
                warm_up_state_age_hours=input_metadata.warm_up_state_age_hours,
                observation_staleness_hours=input_metadata.observation_staleness_hours,
                ensemble=ensemble,
                created_at=now,
                updated_at=now,
                qc_status=qc_status,
                qc_flags=tuple(flags),
                input_quality=input_quality,
                input_quality_flags=input_quality_flags,
            )
            forecasts.append(forecast)

        return StationForecastResult(
            station_id=station_id,
            model_id=assignment.model_id,
            artifact_id=artifact_id,
            forecasts=forecasts,
            new_state=new_state,
            ensembles=dict(ensembles),
        )

    log.warning(
        "run_station_forecast.all_models_failed",
        station_id=str(station_id),
    )
    return None
