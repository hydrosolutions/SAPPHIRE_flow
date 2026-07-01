from __future__ import annotations

import random  # noqa: TC003
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

import structlog

from sapphire_flow.services.ensemble_fanout import (
    ensembles_only,
    fan_out_ensemble,
    reject_prior_state_for_fanout,
    reject_stateful_ensemble_states,
)
from sapphire_flow.services.input_quality import assess_input_quality
from sapphire_flow.services.operational_inputs import (
    OperationalInputMetadata,  # noqa: TC001
)
from sapphire_flow.types.enums import EnsembleMode, ForecastStatus, QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import (
    FALLBACK_PRIORITY_THRESHOLD,
    ArtifactId,
    ForecastId,
    ModelId,
    StationId,
)

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import ModelArtifactStore
    from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import (
        ClimBaseline,
        ForecastQcRuleSet,
        QcFlag,
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


@dataclass(frozen=True, kw_only=True, slots=True)
class MultiModelForecastResult:
    station_id: StationId
    results: dict[ModelId, StationForecastResult]
    priorities: dict[ModelId, int]
    primary_model_id: ModelId | None
    failed_models: dict[ModelId, str]

    @property
    def combinable_results(self) -> dict[ModelId, StationForecastResult]:
        return {
            mid: r
            for mid, r in self.results.items()
            if self.priorities.get(mid, 0) < FALLBACK_PRIORITY_THRESHOLD
        }


def worst_qc_status(flags: list[QcFlag]) -> QcStatus:
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


def _run_single_model(
    station_id: StationId,
    assignment: ModelAssignment,
    inputs: StationModelInputs,
    input_metadata: OperationalInputMetadata,
    models: dict[ModelId, ForecastModel],
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
) -> StationForecastResult | str:
    model = models.get(assignment.model_id)
    if model is None:
        log.warning(
            "run_station_forecast.model_not_found",
            station_id=str(station_id),
            model_id=str(assignment.model_id),
        )
        return f"model {assignment.model_id} not found in registry"

    artifact_result = artifact_store.fetch_active_artifact_for_station(
        station_id, assignment.model_id
    )
    if artifact_result is None:
        log.warning(
            "run_station_forecast.no_active_artifact",
            station_id=str(station_id),
            model_id=str(assignment.model_id),
        )
        return f"no active artifact for model {assignment.model_id}"

    artifact_id, artifact_bytes = artifact_result

    is_ensemble = (
        model.data_requirements.ensemble_mode is EnsembleMode.ENSEMBLE  # type: ignore[union-attr]
    )
    if is_ensemble:
        # INPUT-side complement of the output-side stateful check below: the
        # fan-out would forward the SAME aggregate ``prior_state`` into every
        # member's ``predict`` (no way to split one aggregate state per member),
        # so a stateful ensemble model on the input side is unsupported. Raise
        # OUTSIDE the ``try`` so it propagates loudly rather than being swallowed
        # into a graceful "predict failed" string.
        reject_prior_state_for_fanout(input_metadata.prior_state)

    ensemble_member_states: list[bytes | None] | None = None
    try:
        artifact = model.deserialize_artifact(artifact_bytes)  # type: ignore[union-attr]
        if is_ensemble:
            # Member-suffixed forcing is fanned out into one N-member ensemble.
            # The fan-out maps ``predict`` over N members; each may return its own
            # ``new_state``. Capture them for the loud-fail check below.
            # ``prior_state`` is guaranteed ``None`` here (guard above).
            predict_fn = ensembles_only(
                model.predict,  # type: ignore[union-attr]
                artifact,
                None,
            )
            ensembles = fan_out_ensemble(
                predict_fn,
                inputs,
                rng,
                future_features=model.data_requirements.future_dynamic_features,  # type: ignore[union-attr]
            )
            ensemble_member_states = predict_fn.states
            new_state = None
        else:
            ensembles, new_state = model.predict(  # type: ignore[union-attr]
                artifact,
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
        return f"predict failed: {exc}"

    # Combining N per-member warm-up states into one aggregate is ill-defined.
    # Stateless ensemble models (all per-member states ``None``) lose nothing —
    # report ``new_state = None``. But a NON-None per-member state means a stateful
    # ensemble model, which is unsupported: fail loudly rather than silently drop.
    if ensemble_member_states is not None:
        reject_stateful_ensemble_states(ensemble_member_states)

    all_flags: dict[str, list] = {}
    for param, ensemble in ensembles.items():
        flags = qc_checker.check(ensemble, qc_rules, qc_overrides, baselines)
        all_flags[param] = flags
        worst = worst_qc_status(flags)
        if worst == QcStatus.QC_FAILED:
            log.warning(
                "run_station_forecast.qc_failed",
                station_id=str(station_id),
                model_id=str(assignment.model_id),
                parameter=param,
            )
            return f"QC failed for parameter {param}"

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
        qc_status = worst_qc_status(flags)
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


def run_all_station_forecasts(
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
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
) -> MultiModelForecastResult:
    sorted_assignments = sorted(assignments, key=lambda a: a.priority)

    results: dict[ModelId, StationForecastResult] = {}
    priorities: dict[ModelId, int] = {}
    failed_models: dict[ModelId, str] = {}
    primary_model_id: ModelId | None = None

    for assignment in sorted_assignments:
        priorities[assignment.model_id] = assignment.priority
        outcome = _run_single_model(
            station_id=station_id,
            assignment=assignment,
            inputs=inputs,
            input_metadata=input_metadata,
            models=models,
            artifact_store=artifact_store,
            qc_checker=qc_checker,
            qc_rules=qc_rules,
            qc_overrides=qc_overrides,
            baselines=baselines,
            nwp_cycle_reference_time=nwp_cycle_reference_time,
            nwp_cycle_source=nwp_cycle_source,
            config=config,
            clock=clock,
            id_gen=id_gen,
            rng=rng,
        )
        if isinstance(outcome, StationForecastResult):
            results[assignment.model_id] = outcome
            if primary_model_id is None:
                primary_model_id = assignment.model_id
        else:
            failed_models[assignment.model_id] = outcome

    return MultiModelForecastResult(
        station_id=station_id,
        results=results,
        priorities=priorities,
        primary_model_id=primary_model_id,
        failed_models=failed_models,
    )


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
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
) -> StationForecastResult | None:
    multi = run_all_station_forecasts(
        station_id=station_id,
        inputs=inputs,
        input_metadata=input_metadata,
        assignments=assignments,
        models=models,
        artifact_store=artifact_store,
        qc_checker=qc_checker,
        qc_rules=qc_rules,
        qc_overrides=qc_overrides,
        baselines=baselines,
        nwp_cycle_reference_time=nwp_cycle_reference_time,
        nwp_cycle_source=nwp_cycle_source,
        config=config,
        clock=clock,
        id_gen=id_gen,
        rng=rng,
    )
    if multi.primary_model_id is None:
        log.warning(
            "run_station_forecast.all_models_failed", station_id=str(station_id)
        )
        return None
    return multi.results[multi.primary_model_id]
