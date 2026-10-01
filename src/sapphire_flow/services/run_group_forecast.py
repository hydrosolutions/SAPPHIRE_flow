from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from math import isnan
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import (
    GroupForecastError,
    InsufficientDataError,
    ModelOutputError,
    StoreError,
)
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
from sapphire_flow.services.training_data import expected_past_buckets
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


class GroupMemberInputReason(Enum):
    CADENCE_MISMATCH = "cadence_mismatch"
    MISSING_DECLARED_STATIC = "missing_declared_static"
    MISSING_REQUIRED_TARGET = "missing_required_target"
    DECLARED_STATIC_NOT_REPRESENTABLE = "declared_static_not_representable"
    INSUFFICIENT_FUTURE = "insufficient_future"
    INPUTS_UNAVAILABLE = "inputs_unavailable"
    MISSING_FORECAST_BINDING = "missing_forecast_binding"


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupMemberInputIssue:
    station_id: StationId
    reason: GroupMemberInputReason


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupInputAssembly:
    expected_station_ids: tuple[StationId, ...]
    inputs: GroupModelInputs | None
    metadata_by_station: dict[StationId, OperationalInputMetadata]
    unavailable_members: tuple[GroupMemberInputIssue, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupForecastOutcome:
    """Plan 404 T2 — `run_group_forecast`'s return: passing stations' results
    AND every rejected station's payload, D3 (group rejections are per
    station)."""

    results: dict[StationId, StationForecastResult]
    rejected: tuple[RejectedAssignmentPayload, ...] = ()
    expected_station_ids: tuple[StationId, ...] = ()
    unavailable_members: tuple[GroupMemberInputIssue, ...] = ()


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


def _missing_static(static: pl.DataFrame | None, names: list[str]) -> bool:
    if static is None or static.is_empty():
        return True
    return any(
        name not in static.columns
        or static[name][0] is None
        or (isinstance(static[name][0], float) and isnan(static[name][0]))
        for name in names
    )


def _reconcile_static_numbers(
    members: list[tuple[StationModelInputs, OperationalInputMetadata]],
    skip: Callable[[StationId, GroupMemberInputReason], None],
) -> list[tuple[StationModelInputs, OperationalInputMetadata]]:
    frames = [inp.data.static for inp, _ in members if inp.data.static is not None]
    if not frames:
        return members
    mixed = [
        name
        for name in frames[0].columns
        if {frame.schema[name] for frame in frames} == {pl.Int64, pl.Float64}
    ]
    result: list[tuple[StationModelInputs, OperationalInputMetadata]] = []
    for inp, metadata in members:
        static = inp.data.static
        if static is not None and mixed:
            if any(
                static.schema[name] == pl.Int64 and abs(static[name][0]) > 2**53
                for name in mixed
            ):
                skip(
                    inp.station_id,
                    GroupMemberInputReason.DECLARED_STATIC_NOT_REPRESENTABLE,
                )
                continue
            static = static.with_columns(
                pl.col(name).cast(pl.Float64) for name in mixed
            )
            inp = replace(inp, data=replace(inp.data, static=static))
        result.append((inp, metadata))
    return result


def _conform_past_frames(
    frames: list[tuple[StationId, pl.DataFrame]],
    features: frozenset[str],
    reference: StationModelInputs,
    lookback_steps: int,
) -> list[tuple[StationId, pl.DataFrame]]:
    if not features:
        return [(sid, pl.DataFrame()) for sid, _ in frames]
    names = sorted(features)
    allowed = {"timestamp", *names}
    for _, frame in frames:
        if unexpected := set(frame.columns) - allowed:
            raise pl.exceptions.SchemaError(
                f"Unexpected group input columns: {sorted(unexpected)}"
            )
    dtypes = {
        name: next(
            (
                frame.schema[name]
                for _, frame in frames
                if name in frame.columns and frame.schema[name] != pl.Null
            ),
            pl.Float64,
        )
        for name in names
    }
    timestamp_dtype = next(
        (
            frame.schema["timestamp"]
            for _, frame in frames
            if "timestamp" in frame.columns
        ),
        pl.Datetime("us", "UTC"),
    )
    result: list[tuple[StationId, pl.DataFrame]] = []
    for sid, frame in frames:
        missing = set(names) - set(frame.columns)
        if missing or frame.is_empty():
            grid = pl.DataFrame(
                {
                    "timestamp": pl.Series(
                        list(
                            expected_past_buckets(
                                reference.issue_time,
                                reference.time_step,
                                lookback_steps,
                            )
                        ),
                        dtype=timestamp_dtype,
                    )
                }
            )
            frame = (
                grid.join(frame, on="timestamp", how="full", coalesce=True).sort(
                    "timestamp"
                )
                if "timestamp" in frame.columns
                else grid
            )
            frame = frame.with_columns(
                pl.lit(None, dtype=dtypes[name]).alias(name) for name in sorted(missing)
            )
        frame = frame.with_columns(
            pl.col(name).cast(dtypes[name])
            for name in names
            if frame.schema[name] == pl.Null
        ).select("timestamp", *names)
        result.append((sid, frame))
    return result


def _future_adequate(
    future: pl.DataFrame,
    model: GroupForecastModel,
    model_id: ModelId,
    group: StationGroup,
    station_id: StationId,
) -> bool:
    requirements = model.data_requirements
    if not requirements.future_dynamic_features:
        return True
    required_steps = resolve_required_steps(
        model, model_id, requirements.forecast_horizon_steps
    ).steps
    coverage = assess_future_coverage(
        future,
        required_features=requirements.future_dynamic_features,
        required_steps=required_steps,
        ensemble_mode=requirements.ensemble_mode,
    )
    if not coverage.adequate:
        log.warning(
            "nwp.insufficient_coverage",
            group_id=str(group.id),
            model_id=str(model_id),
            station_id=str(station_id),
            required_steps=required_steps,
            available_steps=coverage.available_steps,
            detail=coverage.detail,
        )
    return coverage.adequate


def _select_group_members(
    inputs: GroupModelInputs, station_ids: tuple[StationId, ...]
) -> GroupModelInputs:
    ids = [str(sid) for sid in station_ids]

    def select(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.filter(pl.col(_STATION_ID_COLUMN).is_in(ids))

    return replace(
        inputs,
        station_ids=station_ids,
        past_targets=select(inputs.past_targets),
        past_dynamic=select(inputs.past_dynamic),
        future_dynamic=select(inputs.future_dynamic),
        static=select(inputs.static) if inputs.static is not None else None,
        source_evidence=tuple(
            (sid, evidence)
            for sid, evidence in inputs.source_evidence
            if sid in station_ids
        ),
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
    assembly = assemble_group_operational_inputs_outcome(
        group=group,
        model=model,
        model_id=model_id,
        issue_time=issue_time,
        cycle_time=cycle_time,
        nwp_source_by_station=nwp_source_by_station,
        forcing_source=forcing_source,
        weather_forecast_store=weather_forecast_store,
        obs_store=obs_store,
        station_store=station_store,
        basin_store=basin_store,
        model_state_store=model_state_store,
        clock=clock,
        forecast_horizon_steps=forecast_horizon_steps,
        time_step=time_step,
    )
    if assembly.inputs is None:
        return None
    return assembly.inputs, assembly.metadata_by_station


def assemble_group_operational_inputs_outcome(
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
) -> GroupInputAssembly:
    expected = tuple(sorted(group.station_ids, key=str))
    unavailable: list[GroupMemberInputIssue] = []
    serviceable: list[tuple[StationModelInputs, OperationalInputMetadata]] = []
    requirements = model.data_requirements

    def skip(sid: StationId, reason: GroupMemberInputReason) -> None:
        unavailable.append(GroupMemberInputIssue(station_id=sid, reason=reason))
        log.warning(
            "run_group_forecast.station_inputs_unavailable",
            group_id=str(group.id),
            station_id=str(sid),
            model_id=str(model_id),
            issue_time=str(issue_time),
            reason=reason.value,
        )

    for sid in expected:
        if sid not in nwp_source_by_station:
            skip(sid, GroupMemberInputReason.MISSING_FORECAST_BINDING)
            continue
        try:
            result = assemble_station_operational_inputs(
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
            )
        except StoreError:
            raise
        except InsufficientDataError:
            # Defensive service contract; current assemblers mostly return None
            # or empty frames for anticipated shortages.
            skip(sid, GroupMemberInputReason.INPUTS_UNAVAILABLE)
            continue
        except Exception as exc:
            _raise_store_error_if_connection_fatal(
                exc, group=group, model_id=model_id, operation="input_assembly"
            )
            raise
        if result is None:
            skip(sid, GroupMemberInputReason.CADENCE_MISMATCH)
            continue
        station_input, metadata = result
        required_targets = requirements.required_past_targets
        targets = station_input.data.past_targets
        if required_targets and (
            targets.is_empty() or not required_targets.issubset(targets.columns)
        ):
            skip(sid, GroupMemberInputReason.MISSING_REQUIRED_TARGET)
            continue
        static = station_input.data.static
        names = sorted(requirements.static_features)
        if names and _missing_static(static, names):
            skip(sid, GroupMemberInputReason.MISSING_DECLARED_STATIC)
            continue
        projected = static.select(names) if names and static is not None else None
        station_input = replace(
            station_input, data=replace(station_input.data, static=projected)
        )
        if not _future_adequate(
            station_input.data.future_dynamic, model, model_id, group, sid
        ):
            skip(sid, GroupMemberInputReason.INSUFFICIENT_FUTURE)
            continue
        serviceable.append((station_input, metadata))

    serviceable = _reconcile_static_numbers(serviceable, skip)
    if not serviceable:
        log.warning(
            "run_group_forecast.no_serviceable_stations",
            group_id=str(group.id),
            model_id=str(model_id),
            issue_time=str(issue_time),
        )
        return GroupInputAssembly(
            expected_station_ids=expected,
            inputs=None,
            metadata_by_station={},
            unavailable_members=tuple(unavailable),
        )

    station_inputs = [inputs for inputs, _ in serviceable]
    _assert_consistent_station_inputs(station_inputs)
    first = station_inputs[0]
    # Output target names do not imply input history. Project only when the
    # model provides explicit past-known declarations; native legacy models
    # without that declaration retain their original target frames.
    target_names = sorted(requirements.required_past_targets or ())
    past_targets = [
        (
            inp.station_id,
            (
                inp.data.past_targets.select("timestamp", *target_names)
                if target_names
                else pl.DataFrame()
            )
            if requirements.required_past_targets is not None
            else inp.data.past_targets,
        )
        for inp in station_inputs
    ]
    past_dynamic = _conform_past_frames(
        [(inp.station_id, inp.data.past_dynamic) for inp in station_inputs],
        requirements.past_dynamic_features,
        first,
        requirements.lookback_steps,
    )
    metadata_by_station = {
        inp.station_id: replace(
            metadata, past_forcing_before_conformance=inp.data.past_dynamic
        )
        for inp, metadata in serviceable
    }
    static_parts = [
        (inp.station_id, inp.data.static)
        for inp in station_inputs
        if inp.data.static is not None
    ]
    inputs = GroupModelInputs(
        group_id=group.id,
        station_ids=tuple(inp.station_id for inp in station_inputs),
        past_targets=_stack_station_frames(past_targets),
        past_dynamic=_stack_station_frames(past_dynamic),
        future_dynamic=_stack_station_frames(
            [
                (
                    inp.station_id,
                    inp.data.future_dynamic.select(
                        "timestamp",
                        *sorted(set(inp.data.future_dynamic.columns) - {"timestamp"}),
                    )
                    if requirements.future_dynamic_features
                    else pl.DataFrame(),
                )
                for inp in station_inputs
            ]
        ),
        static=_stack_station_frames(static_parts) if static_parts else None,
        issue_time=first.issue_time,
        forecast_horizon_steps=first.forecast_horizon_steps,
        time_step=first.time_step,
        source_evidence=tuple(
            (inp.station_id, inp.source_evidence)
            for inp in station_inputs
            if inp.source_evidence is not None
        ),
    )
    return GroupInputAssembly(
        expected_station_ids=expected,
        inputs=inputs,
        metadata_by_station=metadata_by_station,
        unavailable_members=tuple(unavailable),
    )


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
        past_dynamic=(
            input_metadata.past_forcing_before_conformance
            if input_metadata.past_forcing_before_conformance is not None
            else group_inputs.for_station(station_id).past_dynamic
        ),
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
    metadata_by_station: dict[StationId, OperationalInputMetadata],
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
        metadata = metadata_by_station.get(station_id)
        original = (
            metadata.past_forcing_before_conformance if metadata is not None else None
        )
        flags = past_forcing_flags(
            past_dynamic=original
            if original is not None
            else group_inputs.for_station(station_id).past_dynamic,
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
    unavailable_members: tuple[GroupMemberInputIssue, ...] = (),
) -> GroupForecastOutcome:
    expected = tuple(sorted(group.station_ids, key=str))
    unavailable = list(unavailable_members)
    serviceable_ids: list[StationId] = []
    for sid in group_inputs.station_ids:
        if _future_adequate(
            group_inputs.for_station(sid).future_dynamic,
            model,
            assignment.model_id,
            group,
            sid,
        ):
            serviceable_ids.append(sid)
        else:
            unavailable.append(
                GroupMemberInputIssue(
                    station_id=sid, reason=GroupMemberInputReason.INSUFFICIENT_FUTURE
                )
            )
    if not serviceable_ids:
        return GroupForecastOutcome(
            results={},
            expected_station_ids=expected,
            unavailable_members=tuple(unavailable),
        )
    group_inputs = _select_group_members(group_inputs, tuple(serviceable_ids))

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
        return GroupForecastOutcome(
            results={},
            expected_station_ids=expected,
            unavailable_members=tuple(unavailable),
        )

    if artifact_result is None:
        log.warning(
            "run_group_forecast.no_active_artifact",
            group_id=str(group.id),
            model_id=str(assignment.model_id),
        )
        return GroupForecastOutcome(
            results={},
            expected_station_ids=expected,
            unavailable_members=tuple(unavailable),
        )

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
                metadata_by_station=metadata_by_station,
            ),
        )
        return GroupForecastOutcome(
            results={},
            expected_station_ids=expected,
            unavailable_members=tuple(unavailable),
        )
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
                metadata_by_station=metadata_by_station,
            ),
        )
        return GroupForecastOutcome(
            results={},
            expected_station_ids=expected,
            unavailable_members=tuple(unavailable),
        )

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
                metadata_by_station=metadata_by_station,
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
            metadata_by_station=metadata_by_station,
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

    return GroupForecastOutcome(
        results=results,
        rejected=tuple(rejected_payloads),
        expected_station_ids=expected,
        unavailable_members=tuple(unavailable),
    )
