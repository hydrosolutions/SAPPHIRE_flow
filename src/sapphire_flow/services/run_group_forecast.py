from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import ModelOutputError, StoreError
from sapphire_flow.services.hindcast import is_connection_fatal
from sapphire_flow.services.input_quality import assess_input_quality
from sapphire_flow.services.nwp_coverage import assess_future_coverage
from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.services.run_station_forecast import (
    StationForecastResult,
    worst_qc_status,
)
from sapphire_flow.types.enums import ArtifactScope, ForecastStatus, QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ForecastId
from sapphire_flow.types.model import GroupModelInputs

if TYPE_CHECKING:
    import random
    from collections.abc import Callable
    from datetime import timedelta
    from uuid import UUID

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import GroupForecastModel
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ModelArtifactStore,
        ModelStateStore,
        ObservationStore,
        StationGroupStore,
        StationStore,
        WeatherForecastStore,
    )
    from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
    from sapphire_flow.services.operational_inputs import OperationalInputMetadata
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import (
        ClimBaseline,
        ForecastQcRuleSet,
        QcFlag,
        StationForecastQcOverride,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
    from sapphire_flow.types.model import StationModelInputs
    from sapphire_flow.types.station import GroupModelAssignment, StationGroup

log = structlog.get_logger(__name__)

_STATION_ID_COLUMN = "station_id"


def _station_id_first(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(
        [_STATION_ID_COLUMN, *[col for col in df.columns if col != _STATION_ID_COLUMN]]
    )


def _with_station_id(df: pl.DataFrame, station_id: StationId) -> pl.DataFrame:
    if not df.columns:
        return pl.DataFrame(
            {
                _STATION_ID_COLUMN: pl.Series(
                    [str(station_id)] * df.height,
                    dtype=pl.Utf8,
                )
            }
        )
    return _station_id_first(
        df.with_columns(pl.lit(str(station_id)).alias(_STATION_ID_COLUMN))
    )


def _stack_station_frames(
    frames: list[tuple[StationId, pl.DataFrame]],
) -> pl.DataFrame:
    return pl.concat([_with_station_id(df, sid) for sid, df in frames])


def _assert_consistent_station_inputs(
    station_inputs: list[StationModelInputs],
) -> None:
    first = station_inputs[0]
    for station_input in station_inputs:
        assert station_input.issue_time == first.issue_time, (
            "Inconsistent issue_time for group operational inputs"
        )
        assert station_input.forecast_horizon_steps == first.forecast_horizon_steps, (
            "Inconsistent forecast_horizon_steps for group operational inputs"
        )
        assert station_input.time_step == first.time_step, (
            "Inconsistent time_step for group operational inputs"
        )


def assemble_group_operational_inputs(
    *,
    group: StationGroup,
    model: GroupForecastModel,
    model_id: ModelId,
    issue_time: UtcDatetime,
    cycle_time: UtcDatetime,
    nwp_source_by_station: dict[StationId, str],
    forcing_source: WeatherReanalysisSource,
    weather_forecast_store: WeatherForecastStore,
    obs_store: ObservationStore,
    station_store: StationStore,
    basin_store: BasinStore,
    model_state_store: ModelStateStore,
    clock: Callable[[], UtcDatetime],
    forecast_horizon_steps: int,
    time_step: timedelta,
) -> tuple[GroupModelInputs, dict[StationId, OperationalInputMetadata]] | None:
    station_results = [
        (
            sid,
            assemble_station_operational_inputs(
                station_id=sid,
                model=model,
                model_id=model_id,
                issue_time=issue_time,
                cycle_time=cycle_time,
                nwp_source=nwp_source_by_station[sid],
                forcing_source=forcing_source,
                weather_forecast_store=weather_forecast_store,
                obs_store=obs_store,
                station_store=station_store,
                basin_store=basin_store,
                model_state_store=model_state_store,
                clock=clock,
                forecast_horizon_steps=forecast_horizon_steps,
                time_step=time_step,
            ),
        )
        for sid in sorted(group.station_ids, key=str)
    ]

    skipped_station_ids = [sid for sid, result in station_results if result is None]
    for sid in skipped_station_ids:
        log.warning(
            "run_group_forecast.station_inputs_unavailable",
            group_id=str(group.id),
            station_id=str(sid),
            model_id=str(model_id),
            issue_time=str(issue_time),
        )

    serviceable_results = [
        (sid, inputs, metadata)
        for sid, result in station_results
        if result is not None
        for inputs, metadata in [result]
    ]
    if not serviceable_results:
        log.warning(
            "run_group_forecast.no_serviceable_stations",
            group_id=str(group.id),
            model_id=str(model_id),
            issue_time=str(issue_time),
        )
        return None

    station_inputs = [inputs for _, inputs, _ in serviceable_results]
    metadata_by_station = {sid: metadata for sid, _, metadata in serviceable_results}
    _assert_consistent_station_inputs(station_inputs)

    static_parts = [
        (station_input.station_id, static)
        for station_input in station_inputs
        if (static := station_input.data.static) is not None
    ]

    first = station_inputs[0]
    inputs = GroupModelInputs(
        group_id=group.id,
        station_ids=tuple(station_input.station_id for station_input in station_inputs),
        past_targets=_stack_station_frames(
            [
                (station_input.station_id, station_input.data.past_targets)
                for station_input in station_inputs
            ]
        ),
        past_dynamic=_stack_station_frames(
            [
                (station_input.station_id, station_input.data.past_dynamic)
                for station_input in station_inputs
            ]
        ),
        future_dynamic=_stack_station_frames(
            [
                (station_input.station_id, station_input.data.future_dynamic)
                for station_input in station_inputs
            ]
        ),
        static=_stack_station_frames(static_parts) if static_parts else None,
        issue_time=first.issue_time,
        forecast_horizon_steps=first.forecast_horizon_steps,
        time_step=first.time_step,
    )

    return inputs, metadata_by_station


def discover_group_runs(
    models: dict[ModelId, object],
    group_store: StationGroupStore,
) -> list[tuple[StationGroup, ModelId]]:
    return [
        (group, model_id)
        for model_id, model in models.items()
        if getattr(model, "artifact_scope", None) is ArtifactScope.GROUP
        for group in group_store.fetch_groups_for_model(model_id)
    ]


def _raise_store_error_if_connection_fatal(
    exc: Exception,
    *,
    group: StationGroup,
    model_id: ModelId,
    operation: str,
) -> None:
    if not is_connection_fatal(exc):
        return
    log.warning(
        "run_group_forecast.connection_fatal",
        group_id=str(group.id),
        model_id=str(model_id),
        operation=operation,
        error=str(exc),
    )
    msg = (
        f"Connection-fatal error during group forecast {operation} "
        f"for group {group.id} model {model_id}"
    )
    raise StoreError(msg) from exc


def _build_station_result(
    *,
    station_id: StationId,
    assignment: GroupModelAssignment,
    artifact_id: ArtifactId,
    group_inputs: GroupModelInputs,
    input_metadata: OperationalInputMetadata,
    ensembles: dict[str, ForecastEnsemble],
    new_state: bytes | None,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
) -> StationForecastResult | None:
    all_flags: dict[str, list[QcFlag]] = {}
    for param, ensemble in ensembles.items():
        flags = qc_checker.check(ensemble, qc_rules, qc_overrides, baselines)
        all_flags[param] = flags
        worst = worst_qc_status(flags)
        if worst == QcStatus.QC_FAILED:
            log.warning(
                "run_group_forecast.qc_failed",
                station_id=str(station_id),
                group_id=str(assignment.group_id),
                model_id=str(assignment.model_id),
                parameter=param,
            )
            return None

    iq_config = config.input_quality
    input_quality, input_quality_flags = assess_input_quality(
        observation_staleness_hours=input_metadata.observation_staleness_hours,
        warm_up_source=input_metadata.warm_up_source,
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
        forecasts.append(
            OperationalForecast(
                id=ForecastId(id_gen()),
                station_id=station_id,
                model_id=assignment.model_id,
                model_artifact_id=artifact_id,
                issued_at=group_inputs.issue_time,
                nwp_cycle_reference_time=nwp_cycle_reference_time,
                nwp_cycle_source=nwp_cycle_source,
                representation=ensemble.representation,
                status=ForecastStatus.RAW,
                version=1,
                warm_up_source=input_metadata.warm_up_source,
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
        )

    return StationForecastResult(
        station_id=station_id,
        model_id=assignment.model_id,
        artifact_id=artifact_id,
        forecasts=forecasts,
        new_state=new_state,
        ensembles=dict(ensembles),
    )


def run_group_forecast(
    *,
    group: StationGroup,
    group_inputs: GroupModelInputs,
    metadata_by_station: dict[StationId, OperationalInputMetadata],
    assignment: GroupModelAssignment,
    model: GroupForecastModel,
    artifact_store: ModelArtifactStore,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines_by_station: dict[StationId, list[ClimBaseline]],
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
    rng: random.Random,
) -> dict[StationId, StationForecastResult]:
    # Plan 090 D1/D2/D3 (GROUP path): before predict_batch, a group model that
    # declares future NWP forcing must have adequate coverage for EVERY member
    # station it forecasts — else predict_batch would emit a truncated batch. On
    # shortfall for any station, skip the group model gracefully (return {}) so
    # the fallback chain still runs, mirroring the STATION path.
    future_features = model.data_requirements.future_dynamic_features
    if future_features:
        required_steps = model.data_requirements.forecast_horizon_steps
        ensemble_mode = model.data_requirements.ensemble_mode
        for station_id in group_inputs.station_ids:
            station_future = group_inputs.for_station(station_id).future_dynamic
            coverage = assess_future_coverage(
                station_future,
                required_features=future_features,
                required_steps=required_steps,
                ensemble_mode=ensemble_mode,
            )
            if not coverage.adequate:
                log.warning(
                    "nwp.insufficient_coverage",
                    group_id=str(group.id),
                    model_id=str(assignment.model_id),
                    station_id=str(station_id),
                    required_steps=required_steps,
                    available_steps=coverage.available_steps,
                    detail=coverage.detail,
                )
                return {}

    try:
        artifact_result = artifact_store.fetch_active_artifact(
            assignment.model_id,
            group_id=group.id,
        )
    except StoreError:
        raise
    except Exception as exc:
        _raise_store_error_if_connection_fatal(
            exc,
            group=group,
            model_id=assignment.model_id,
            operation="artifact_fetch",
        )
        log.warning(
            "run_group_forecast.artifact_fetch_failed",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            error=str(exc),
        )
        return {}

    if artifact_result is None:
        log.warning(
            "run_group_forecast.no_active_artifact",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
        )
        return {}

    artifact_id, artifact_bytes = artifact_result

    try:
        artifact = model.deserialize_artifact(artifact_bytes)
        batch_result = model.predict_batch(artifact, group_inputs, rng)
    except ModelOutputError as exc:
        log.warning(
            "run_group_forecast.predict_batch_failed",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            error=str(exc),
        )
        return {}
    except StoreError:
        raise
    except Exception as exc:
        _raise_store_error_if_connection_fatal(
            exc,
            group=group,
            model_id=assignment.model_id,
            operation="predict_batch",
        )
        log.warning(
            "run_group_forecast.predict_batch_failed",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            error=str(exc),
        )
        return {}

    expected_station_ids = set(group_inputs.station_ids)
    if not batch_result:
        log.warning(
            "run_group_forecast.batch_empty",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
        )

    missing_station_ids = sorted(expected_station_ids - set(batch_result), key=str)
    if missing_station_ids:
        log.warning(
            "run_group_forecast.batch_missing_station_outputs",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            station_ids=[str(station_id) for station_id in missing_station_ids],
        )

    results: dict[StationId, StationForecastResult] = {}
    for station_id, (ensembles, new_state) in batch_result.items():
        input_metadata = metadata_by_station.get(station_id)
        if station_id not in expected_station_ids or input_metadata is None:
            log.warning(
                "run_group_forecast.batch_unexpected_station_output",
                group_id=str(group.id),
                model_id=str(assignment.model_id),
                station_id=str(station_id),
            )
            continue
        station_result = _build_station_result(
            station_id=station_id,
            assignment=assignment,
            artifact_id=artifact_id,
            group_inputs=group_inputs,
            input_metadata=input_metadata,
            ensembles=ensembles,
            new_state=new_state,
            qc_checker=qc_checker,
            qc_rules=qc_rules,
            qc_overrides=qc_overrides,
            baselines=baselines_by_station.get(station_id, []),
            nwp_cycle_reference_time=nwp_cycle_reference_time,
            nwp_cycle_source=nwp_cycle_source,
            config=config,
            clock=clock,
            id_gen=id_gen,
        )
        if station_result is not None:
            results[station_id] = station_result

    return results
