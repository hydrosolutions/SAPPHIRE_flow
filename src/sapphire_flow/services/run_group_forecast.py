from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import GroupForecastError, ModelOutputError, StoreError
from sapphire_flow.services.forecast_evidence import capture_group_evidence
from sapphire_flow.services.hindcast import is_connection_fatal
from sapphire_flow.services.horizon_semantics import resolve_required_steps
from sapphire_flow.services.input_quality import (
    assess_input_quality,
    past_forcing_flags,
)
from sapphire_flow.services.nwp_coverage import assess_future_coverage
from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.services.run_station_forecast import (
    StationForecastResult,
    check_forecast_parameter,
    worst_qc_status,
)
from sapphire_flow.types.domain import aggregate_input_quality
from sapphire_flow.types.enums import ArtifactScope, ForecastStatus, QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ForecastId
from sapphire_flow.types.model import GroupModelInputs
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedParameterPayload,
)

if TYPE_CHECKING:
    import random
    from collections.abc import Callable
    from datetime import timedelta
    from uuid import UUID

    from sapphire_flow.config.deployment import DeploymentConfig, InputQualityConfig
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
    from sapphire_flow.types.forecast_evidence import ForecastEvidence
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
    from sapphire_flow.types.model import ModelDataRequirements, StationModelInputs
    from sapphire_flow.types.station import GroupModelAssignment, StationGroup

log = structlog.get_logger(__name__)

_STATION_ID_COLUMN = "station_id"


@dataclass(frozen=True, kw_only=True, slots=True)
class _StationResultOutcome:
    """Plan 404 T2 — `_build_station_result`'s return: a passing station's
    result, or (on QC_FAILED) `None` plus its rejected payload. Exactly one
    of the two is set."""

    result: StationForecastResult | None
    rejected: RejectedAssignmentPayload | None


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupForecastOutcome:
    """Plan 404 T2 — `run_group_forecast`'s return: passing stations' results
    AND every rejected station's payload, D3 (group rejections are per
    station)."""

    results: dict[StationId, StationForecastResult]
    rejected: tuple[RejectedAssignmentPayload, ...] = ()


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
        source_evidence=tuple(
            (station_input.station_id, station_input.source_evidence)
            for station_input in station_inputs
            if station_input.source_evidence is not None
        ),
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
    data_requirements: ModelDataRequirements,
    ensembles: dict[str, ForecastEnsemble],
    new_state: bytes | None,
    evidence: ForecastEvidence,
    qc_checker: ForecastOutputQualityChecker,
    qc_rules: ForecastQcRuleSet,
    qc_overrides: list[StationForecastQcOverride],
    baselines: list[ClimBaseline],
    water_level_datum_masl: float | None,
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    config: DeploymentConfig,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
) -> _StationResultOutcome:
    # Plan 404 T2 — QC runs on EVERY parameter before the verdict (mirrors
    # `run_station_forecast.py::check_forecast_parameter`'s member-path
    # restructuring). `rejected_parameters` carries every parameter's own
    # verdict + flags, for the rejected-forecast record if this station ends
    # up QC_FAILED; unused (discarded) on a passing station. Once a
    # parameter has failed, an error anywhere in a REMAINING parameter's
    # block is logged and that parameter is recorded QC_UNCHECKED — never
    # escaping this function (it never drops the whole group).
    all_flags: dict[str, list[QcFlag]] = {}
    rejected_parameters: list[RejectedParameterPayload] = []
    failed_params: list[str] = []
    verdict_failed = False
    for param, ensemble in ensembles.items():
        datum = water_level_datum_masl if param == "water_level" else None
        if verdict_failed:
            try:
                flags = check_forecast_parameter(
                    ensemble,
                    param,
                    datum,
                    qc_checker,
                    qc_rules,
                    qc_overrides,
                    baselines,
                )
            except Exception as exc:
                log.error(
                    "run_group_forecast.qc_parameter_unchecked",
                    station_id=str(station_id),
                    model_id=str(assignment.model_id),
                    parameter=param,
                    error=str(exc),
                )
                all_flags[param] = []
                rejected_parameters.append(
                    RejectedParameterPayload(
                        ensemble=ensemble, qc_status=QcStatus.QC_UNCHECKED, qc_flags=()
                    )
                )
                continue
        else:
            flags = check_forecast_parameter(
                ensemble, param, datum, qc_checker, qc_rules, qc_overrides, baselines
            )

        all_flags[param] = flags
        worst = worst_qc_status(flags)
        rejected_parameters.append(
            RejectedParameterPayload(
                ensemble=ensemble, qc_status=worst, qc_flags=tuple(flags)
            )
        )
        if worst == QcStatus.QC_FAILED:
            verdict_failed = True
            failed_params.append(param)

    if verdict_failed:
        log.warning(
            "run_group_forecast.qc_failed",
            station_id=str(station_id),
            group_id=str(assignment.group_id),
            model_id=str(assignment.model_id),
            parameters=failed_params,
        )
        return _StationResultOutcome(
            result=None,
            rejected=RejectedAssignmentPayload(
                station_id=station_id,
                model_id=assignment.model_id,
                model_artifact_id=artifact_id,
                issued_at=group_inputs.issue_time,
                group_id=assignment.group_id,
                parameters=tuple(rejected_parameters),
            ),
        )

    iq_config = config.input_quality
    input_quality, input_quality_flags = assess_input_quality(
        observation_staleness_hours=input_metadata.observation_staleness_hours,
        observation_qc_coverage=input_metadata.observation_qc_coverage,
        warm_up_source=input_metadata.warm_up_source,
        warm_up_state_age_hours=input_metadata.warm_up_state_age_hours,
        nwp_cycle_source=nwp_cycle_source,
        nwp_age_hours=input_metadata.nwp_age_hours,
        obs_partial_hours=config.observation_staleness_warning_hours,
        config=iq_config,
        warmup_partial_hours=iq_config.warmup_snapshot_age_partial_hours,
        warmup_degraded_hours=iq_config.warmup_snapshot_age_degraded_hours,
    )

    # Plan 239 T1b: past-forcing gaps become an input-quality flag, never a
    # refusal (owner decision 2026-09-08). This function is already invoked
    # PER STATION, so the flags describe THIS station only — a gap at one
    # station of a group must never label its siblings.
    forcing_flags = past_forcing_flags(
        past_dynamic=group_inputs.for_station(station_id).past_dynamic,
        features=data_requirements.past_dynamic_features,
        anchor=group_inputs.issue_time,
        time_step=group_inputs.time_step,
        lookback_steps=data_requirements.lookback_steps,
        recent_steps=iq_config.forcing_recent_steps,
        declared_lookbacks=dict(data_requirements.declared_lookbacks),
    )
    if forcing_flags:
        input_quality_flags = (*input_quality_flags, *forcing_flags)
        input_quality = aggregate_input_quality(list(input_quality_flags))

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
                evidence=evidence,
            )
        )

    return _StationResultOutcome(
        result=StationForecastResult(
            station_id=station_id,
            model_id=assignment.model_id,
            artifact_id=artifact_id,
            forecasts=forecasts,
            new_state=new_state,
            ensembles=dict(ensembles),
        ),
        rejected=None,
    )


def _group_forcing_gap_details(
    *,
    group_inputs: GroupModelInputs,
    data_requirements: ModelDataRequirements,
    iq_config: InputQualityConfig,
) -> dict[str, list[str]]:
    """Per-station past-forcing gaps, for a batch that already failed.

    Plan 239 T1b review (blocker, 2026-09-09): `predict_batch` failing returns
    ``{}`` for the WHOLE group, so no per-station result is ever built and the
    forcing flags computed in that builder never exist. The gap analysis is
    what explains a short-window refusal, so it is recomputed here — only on
    the failure path, so the happy path pays nothing.

    Stations with no gaps are omitted; an empty dict means forcing was intact
    and the batch failed for some other reason.
    """
    details: dict[str, list[str]] = {}
    declared = dict(data_requirements.declared_lookbacks)
    for station_id in group_inputs.station_ids:
        flags = past_forcing_flags(
            past_dynamic=group_inputs.for_station(station_id).past_dynamic,
            features=data_requirements.past_dynamic_features,
            anchor=group_inputs.issue_time,
            time_step=group_inputs.time_step,
            lookback_steps=data_requirements.lookback_steps,
            recent_steps=iq_config.forcing_recent_steps,
            declared_lookbacks=declared,
        )
        if flags:
            details[str(station_id)] = [flag.detail for flag in flags]
    return details


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
    water_level_datums_masl: dict[StationId, float | None] | None = None,
) -> GroupForecastOutcome:
    # Plan 090 D1/D2/D3 (GROUP path): before predict_batch, a group model that
    # declares future NWP forcing must have adequate coverage for EVERY member
    # station it forecasts — else predict_batch would emit a truncated batch. On
    # shortfall for any station, skip the group model gracefully (empty
    # outcome) so the fallback chain still runs, mirroring the STATION path.
    future_features = model.data_requirements.future_dynamic_features
    if future_features:
        # Plan 159 T0d (INTERIM): a model's declared horizon may be a CEILING rather
        # than a floor. Strict by default; see `services/horizon_semantics.py`.
        horizon = resolve_required_steps(
            model,
            assignment.model_id,
            model.data_requirements.forecast_horizon_steps,
        )
        required_steps = horizon.steps
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
                return GroupForecastOutcome(results={})

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
        return GroupForecastOutcome(results={})

    if artifact_result is None:
        log.warning(
            "run_group_forecast.no_active_artifact",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
        )
        return GroupForecastOutcome(results={})

    artifact_id, artifact_bytes = artifact_result
    rng_state = rng.getstate()
    evidence = capture_group_evidence(
        inputs=group_inputs,
        model=model,
        model_id=assignment.model_id,
        artifact_bytes=artifact_bytes,
        rng_state=rng_state,
        config=config,
        qc_rules=qc_rules,
        qc_overrides=qc_overrides,
        baselines_by_station=baselines_by_station,
        water_level_datums_masl=water_level_datums_masl or {},
    )

    try:
        artifact = model.deserialize_artifact(artifact_bytes)
        batch_result = model.predict_batch(artifact, group_inputs, rng)
    except ModelOutputError as exc:
        log.warning(
            "run_group_forecast.predict_batch_failed",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            error=str(exc),
            forcing_gaps=_group_forcing_gap_details(
                group_inputs=group_inputs,
                data_requirements=model.data_requirements,
                iq_config=config.input_quality,
            ),
        )
        return GroupForecastOutcome(results={})
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
            forcing_gaps=_group_forcing_gap_details(
                group_inputs=group_inputs,
                data_requirements=model.data_requirements,
                iq_config=config.input_quality,
            ),
        )
        return GroupForecastOutcome(results={})

    expected_station_ids = set(group_inputs.station_ids)
    if not batch_result:
        log.warning(
            "run_group_forecast.batch_empty",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            forcing_gaps=_group_forcing_gap_details(
                group_inputs=group_inputs,
                data_requirements=model.data_requirements,
                iq_config=config.input_quality,
            ),
        )

    missing_station_ids = sorted(expected_station_ids - set(batch_result), key=str)
    if missing_station_ids:
        # Cross-check review 2026-09-09: a batch can come back PARTIAL rather
        # than failing — the FI adapter skips a station whose variables all
        # report FAILURE and returns its successful siblings. That station gets
        # no per-station result, so the forcing flags built in the result
        # builder never exist for it. Diagnose exactly the ones that vanished.
        gaps = _group_forcing_gap_details(
            group_inputs=group_inputs,
            data_requirements=model.data_requirements,
            iq_config=config.input_quality,
        )
        log.warning(
            "run_group_forecast.batch_missing_station_outputs",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
            station_ids=[str(station_id) for station_id in missing_station_ids],
            forcing_gaps={
                str(sid): gaps[str(sid)]
                for sid in missing_station_ids
                if str(sid) in gaps
            },
        )

    results: dict[StationId, StationForecastResult] = {}
    rejected_payloads: list[RejectedAssignmentPayload] = []
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
        # Plan 404 T2 — a station RAISING here (an ordinary, unanticipated
        # error, not a QC rejection — `_build_station_result` never raises
        # for a QC_FAILED parameter, only returns it) must not silently drop
        # an EARLIER station's already-collected rejection. `GroupForecastError`
        # carries `rejected_payloads` gathered so far plus the original
        # exception; the flow's handler buffers them and skips the group
        # exactly as the pre-Plan-404 generic handler did.
        try:
            outcome = _build_station_result(
                station_id=station_id,
                assignment=assignment,
                artifact_id=artifact_id,
                group_inputs=group_inputs,
                input_metadata=input_metadata,
                data_requirements=model.data_requirements,
                ensembles=ensembles,
                new_state=new_state,
                evidence=evidence,
                qc_checker=qc_checker,
                qc_rules=qc_rules,
                qc_overrides=qc_overrides,
                baselines=baselines_by_station.get(station_id, []),
                water_level_datum_masl=(water_level_datums_masl or {}).get(station_id),
                nwp_cycle_reference_time=nwp_cycle_reference_time,
                nwp_cycle_source=nwp_cycle_source,
                config=config,
                clock=clock,
                id_gen=id_gen,
            )
        except Exception as exc:
            raise GroupForecastError(
                f"station {station_id} raised while building its group result: {exc}",
                rejected=tuple(rejected_payloads),
                original=exc,
            ) from exc
        if outcome.rejected is not None:
            rejected_payloads.append(outcome.rejected)
        if outcome.result is not None:
            results[station_id] = outcome.result

    return GroupForecastOutcome(results=results, rejected=tuple(rejected_payloads))
