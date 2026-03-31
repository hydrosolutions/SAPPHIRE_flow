from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.services.alert_strategy import (
    PooledEnsembleStrategy,
    PrimaryModelStrategy,
)
from sapphire_flow.types.enums import (
    AlertModelStrategy,
    AlertSource,
    AlertStatus,
    EnsembleRepresentation,
    ThresholdDirection,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.stores import AlertStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import (
        DangerLevelDefinition,
        ExceedanceResult,
        ForecastParameter,
        StationThreshold,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ModelId, StationId

log = structlog.get_logger()

_FORECAST_PARAMETERS: set[ForecastParameter] = {"discharge", "water_level"}

# Tracks strategies that have already logged an unimplemented-fallback warning.
# Keyed on (preferred, actual) to avoid log pollution at scale.
_STRATEGY_FALLBACK_WARNED: set[tuple[AlertModelStrategy, str]] = set()


def check_station_alerts(
    all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]],
    all_thresholds: dict[StationId, list[StationThreshold]],
    danger_levels: list[DangerLevelDefinition],
    all_priorities: dict[StationId, dict[ModelId, int]],
    config: DeploymentConfig,
    alert_store: AlertStore,
    clock: Callable[[], UtcDatetime],
) -> None:
    if not config.enable_forecast_alerts:
        return

    # v0: only raw threshold checking (§A8b). Guard against misconfiguration.
    if config.threshold_check_mode != "raw":
        log.error(
            "alert.check_mode_rejected",
            mode=config.threshold_check_mode,
            reason="flow_3_deferred_v0",
        )
        return

    # Filter to ABOVE-direction danger levels only (v0 — §A8a)
    above_levels = [
        dl for dl in danger_levels if dl.direction == ThresholdDirection.ABOVE
    ]
    skipped = len(danger_levels) - len(above_levels)
    if skipped:
        log.warning("alert.direction_skipped", count=skipped, direction="BELOW")

    t0 = time.perf_counter()
    for station_id, model_ensembles in all_ensembles.items():
        with structlog.contextvars.bound_contextvars(station_id=str(station_id)):
            thresholds = all_thresholds.get(station_id, [])
            priorities = all_priorities.get(station_id, {})
            _check_station(
                station_id,
                model_ensembles,
                thresholds,
                above_levels,
                priorities,
                config,
                alert_store,
                clock,
            )
    log.info(
        "alert.completed",
        duration_ms=round((time.perf_counter() - t0) * 1000, 1),
        stations_checked=len(all_ensembles),
    )


def _unique_parameters(
    model_ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
) -> set[str]:
    return {param for ens in model_ensembles.values() for param in ens}


def _check_station(
    station_id: StationId,
    model_ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
    thresholds: list[StationThreshold],
    danger_levels: list[DangerLevelDefinition],
    priorities: dict[ModelId, int],
    config: DeploymentConfig,
    alert_store: AlertStore,
    clock: Callable[[], UtcDatetime],
) -> None:
    all_results: list[ExceedanceResult] = []
    evaluated_parameters: set[ForecastParameter] = set()

    for raw_parameter in sorted(_unique_parameters(model_ensembles)):
        if raw_parameter not in _FORECAST_PARAMETERS:
            log.debug("alert.parameter_skipped", parameter=raw_parameter)
            continue
        parameter: ForecastParameter = raw_parameter  # type: ignore[assignment]  # narrowed by `in` check above

        param_ensembles = {
            mid: ens[raw_parameter]
            for mid, ens in model_ensembles.items()
            if raw_parameter in ens
        }
        if not param_ensembles:
            continue

        representations = {e.representation for e in param_ensembles.values()}
        strategy, effective_ensembles = _resolve_strategy_and_filter(
            preferred=config.alert_model_strategy,
            param_ensembles=param_ensembles,
            representations=representations,
            priorities=priorities,
        )

        if not _ensemble_size_adequate(
            effective_ensembles, config, station_id, parameter
        ):
            continue

        evaluated_parameters.add(parameter)
        results = strategy.evaluate(
            station_id,
            parameter,
            effective_ensembles,
            thresholds,
            danger_levels,
            priorities,
        )
        all_results.extend(results)

    if evaluated_parameters:
        _process_results(
            all_results,
            station_id,
            evaluated_parameters,
            thresholds,
            alert_store,
            clock,
        )


def _ensemble_size_adequate(
    ensembles: dict[ModelId, ForecastEnsemble],
    config: DeploymentConfig,
    station_id: StationId,
    parameter: ForecastParameter,
) -> bool:
    if not ensembles:
        log.debug(
            "alert.ensemble_not_found", station_id=str(station_id), parameter=parameter
        )
        return False

    representations = {e.representation for e in ensembles.values()}
    if len(representations) > 1:
        raise ValueError(
            f"_ensemble_size_adequate requires homogeneous representations, "
            f"got {representations} for station {station_id} parameter {parameter}"
        )
    total = sum(e.member_count for e in ensembles.values())

    if EnsembleRepresentation.MEMBERS in representations:
        min_required = config.min_operational_ensemble_size
    else:
        min_required = config.min_operational_quantile_levels

    if total < min_required:
        log.warning(
            "alert.ensemble_skipped",
            station_id=str(station_id),
            parameter=parameter,
            reason="ensemble_too_small",
            total=total,
            min_required=min_required,
        )
        return False
    return True


def _resolve_strategy_and_filter(
    preferred: AlertModelStrategy,
    param_ensembles: dict[ModelId, ForecastEnsemble],
    representations: set[EnsembleRepresentation],
    priorities: dict[ModelId, int],
) -> tuple[
    PrimaryModelStrategy | PooledEnsembleStrategy,
    dict[ModelId, ForecastEnsemble],
]:
    n_models = len(param_ensembles)

    if n_models <= 1:
        return PrimaryModelStrategy(), param_ensembles

    is_homogeneous_members = representations == {EnsembleRepresentation.MEMBERS}

    def _select_primary_ensemble() -> dict[ModelId, ForecastEnsemble]:
        primary_id = min(
            param_ensembles.keys(),
            key=lambda mid: (priorities.get(mid, 999), str(mid)),
        )
        return {primary_id: param_ensembles[primary_id]}

    match preferred:
        case AlertModelStrategy.BMA:
            actual = "pooled" if is_homogeneous_members else "primary"
            _warn_fallback_once(preferred, actual, "bma_not_implemented")
            if not is_homogeneous_members:
                return PrimaryModelStrategy(), _select_primary_ensemble()
            return PooledEnsembleStrategy(), param_ensembles
        case AlertModelStrategy.CONSENSUS:
            actual = "pooled" if is_homogeneous_members else "primary"
            _warn_fallback_once(preferred, actual, "consensus_not_implemented")
            if not is_homogeneous_members:
                return PrimaryModelStrategy(), _select_primary_ensemble()
            return PooledEnsembleStrategy(), param_ensembles
        case AlertModelStrategy.POOLED:
            if not is_homogeneous_members:
                log.warning(
                    "alert.strategy_degraded",
                    preferred="pooled",
                    actual="primary",
                    reason="mixed_representations",
                )
                return PrimaryModelStrategy(), _select_primary_ensemble()
            return PooledEnsembleStrategy(), param_ensembles
        case AlertModelStrategy.PRIMARY:
            return PrimaryModelStrategy(), _select_primary_ensemble()
        case _:
            raise ValueError(f"Unhandled strategy: {preferred}")


def _warn_fallback_once(
    preferred: AlertModelStrategy,
    actual: str,
    reason: str,
) -> None:
    key = (preferred, actual)
    if key not in _STRATEGY_FALLBACK_WARNED:
        log.warning(
            "alert.strategy_degraded",
            preferred=preferred.value,
            actual=actual,
            reason=reason,
        )
        _STRATEGY_FALLBACK_WARNED.add(key)


def _process_results(
    results: list[ExceedanceResult],
    station_id: StationId,
    evaluated_parameters: set[ForecastParameter],
    thresholds: list[StationThreshold],
    alert_store: AlertStore,
    clock: Callable[[], UtcDatetime],
) -> None:
    from uuid import uuid4

    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.ids import AlertId

    now = clock()

    # Build mapping: danger_level → set of configured parameters
    level_parameters: dict[str, set[ForecastParameter]] = {}
    for t in thresholds:
        if t.parameter in _FORECAST_PARAMETERS:
            param: ForecastParameter = t.parameter  # type: ignore[assignment]  # narrowed by `in` check above
            level_parameters.setdefault(t.danger_level, set()).add(param)

    # Accumulate model_ids per danger level as union across parameters
    exceeded_models: dict[str, set[ModelId]] = {}
    exceeded_strategy: dict[str, AlertModelStrategy] = {}

    for result in results:
        if result.exceeded:
            if result.danger_level not in exceeded_models:
                exceeded_models[result.danger_level] = set()
                exceeded_strategy[result.danger_level] = result.strategy
            exceeded_models[result.danger_level].update(result.model_ids)

    for level, model_id_set in exceeded_models.items():
        alert_store.upsert_alert(
            Alert(
                id=AlertId(uuid4()),
                station_id=station_id,
                source=AlertSource.FORECAST,
                alert_level=level,
                status=AlertStatus.RAISED,
                trigger_probability=None,
                trigger_value=None,
                triggered_at=now,
                acknowledged_at=None,
                acknowledged_by=None,
                resolved_at=None,
                first_detected_at=now,
                notified_at=None,
                created_at=now,
                model_ids=tuple(sorted(model_id_set, key=str)),
                alert_model_strategy=exceeded_strategy[level],
            )
        )

    # Resolve active alerts for levels that are:
    # (a) not exceeded, AND (b) fully evaluated (all configured params evaluated)
    exceeded_levels = set(exceeded_models.keys())
    active = alert_store.fetch_active_alerts(
        station_id=station_id, source=AlertSource.FORECAST
    )
    for alert in active:
        if alert.alert_level in exceeded_levels:
            continue
        configured = level_parameters.get(alert.alert_level, set())
        if configured and not configured.issubset(evaluated_parameters):
            log.debug(
                "alert.resolution_deferred",
                station_id=str(station_id),
                alert_level=alert.alert_level,
                missing=sorted(configured - evaluated_parameters),
            )
            continue
        alert_store.resolve_alert(alert.id)
