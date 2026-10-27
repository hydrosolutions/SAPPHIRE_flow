from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.exceptions import (
    ConfigurationError,
    DailyOutputRolledBackError,
    GroupForecastError,
    ModelLoadError,
    ModelOutputError,
    StoreError,
    UnsupportedModelRequirementError,
)
from sapphire_flow.flows.daily_forecast_context import current_scheduled_daily_run
from sapphire_flow.services.daily_forecast_candidates import (
    prepare_daily_assignments,
    prepare_daily_candidates,
)
from sapphire_flow.services.daily_forecast_completion import (
    complete_daily_group,
    validate_daily_group,
)
from sapphire_flow.services.daily_forecast_persistence import (
    commit_daily_rejection,
    validate_daily_rejection,
)
from sapphire_flow.services.daily_forecast_preflight import claim_daily_candidates
from sapphire_flow.services.daily_group_provenance import capture_daily_group_provenance
from sapphire_flow.services.daily_weather_cycle import (
    DailyWeatherAssembly,
    assemble_scheduled_daily_weather,
)
from sapphire_flow.services.run_group_forecast import (
    GroupForecastOutcome,
    GroupInputAssembly,
    GroupStatePolicy,
    assemble_group_operational_inputs_outcome,
    run_group_forecast,
)
from sapphire_flow.types.daily_forecast_claim import DailyClaim, DailyFailureReason
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
    DailyOutputSource,
)
from sapphire_flow.types.enums import (
    ArtifactScope,
    FutureWeatherPolicy,
    ModelAssignmentStatus,
)

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Mapping
    from typing import Literal
    from uuid import UUID

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import ForecastModel, GroupForecastModel
    from sapphire_flow.protocols.stores import (
        BasinStore,
        CommittedForecastWriter,
        CommittedRejectedForecastWriter,
        ModelArtifactStore,
        ModelStateStore,
        ObservationStore,
        StationGroupStore,
        StationStore,
        WeatherForecastStore,
    )
    from sapphire_flow.services.daily_forecast_preflight import DailyRuntimeStore
    from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
    from sapphire_flow.types.daily_evaluation import DailyEvaluationSettings
    from sapphire_flow.types.daily_forecast_claim import DailyClaimKey
    from sapphire_flow.types.daily_forecast_one_shot import DailyExecution
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ClimBaseline, ForecastQcRuleSet
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
    from sapphire_flow.types.station import (
        GroupModelAssignment,
        StationGroup,
        StationWeatherSource,
    )

from typing import cast

log = structlog.get_logger(__name__)

# Explicit execution projection. Read authorization/selection and retention are not
# scientific inputs. Assignment priority remains frozen separately.
_EXECUTION_CONFIG_FIELDS = frozenset(
    {
        "daily_evaluation",
        "input_quality",
        "observation_staleness_warning_hours",
        "nwp_max_fallback_age_hours",
        "nwp_cycle_min_age_minutes",
        "nwp_max_wait_hours",
        "reanalysis_source",
        "available_nwp_parameters",
        "available_past_only_nwp_parameters",
        "min_operational_ensemble_size",
        "min_operational_quantile_levels",
        "warm_up_snapshot_max_age_hours",
        "warm_up_snapshot_max_age_monsoon_hours",
        "climatology_window",
        "model_priorities",
    }
)


def execution_configuration_sha256(config: DeploymentConfig) -> str:
    projection = config.model_dump(mode="json", include=set(_EXECUTION_CONFIG_FIELDS))
    for field in ("available_nwp_parameters", "available_past_only_nwp_parameters"):
        projection[field] = sorted(projection[field])
    if projection["daily_evaluation"] is not None:
        for field in ("group_ids", "prefect_deployment_id", "tenant_id", "deployment"):
            projection["daily_evaluation"].pop(field, None)
    return hashlib.sha256(
        json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _failure_reason(exc: Exception) -> DailyFailureReason:
    if isinstance(exc, GroupForecastError):
        return _failure_reason(exc.original)
    if isinstance(exc, (ConfigurationError, UnsupportedModelRequirementError)):
        return DailyFailureReason.CONFIGURATION_UNAVAILABLE
    if isinstance(exc, StoreError):
        return DailyFailureReason.PERSISTENCE_FAILURE
    if isinstance(exc, (ModelOutputError, ValueError)):
        return DailyFailureReason.INTEGRITY_FAILURE
    if isinstance(exc, ModelLoadError):
        return DailyFailureReason.MODEL_FAILURE
    return DailyFailureReason.INTERNAL_FAILURE


class DailyForecastExecution:
    def __init__(
        self,
        *,
        config: DeploymentConfig,
        settings: DailyEvaluationSettings,
        model: GroupForecastModel,
        group_store: StationGroupStore,
        artifact_store: ModelArtifactStore,
        store: DailyRuntimeStore,
        writer: CommittedForecastWriter,
        rejected_writer: CommittedRejectedForecastWriter,
        clock: Callable[[], UtcDatetime],
        id_gen: Callable[[], UUID],
    ) -> None:
        self.config = config
        self.settings = settings
        self.model = model
        self.groups = group_store
        self.artifacts = artifact_store
        self.store = store
        self.writer = writer
        self.rejected_writer = rejected_writer
        self.clock = clock
        self.id_gen = id_gen
        self.pending: dict[UUID, DailyExecution] = {}
        self.errors: list[str] = []
        self.uncertain: set[UUID] = set()
        self.configuration_sha256 = execution_configuration_sha256(config)

    def record_failure(
        self,
        *,
        group_id: StationGroupId,
        attempt_id: UUID | None,
        phase: Literal[
            "preparation",
            "admission_uncertain",
            "admission_refused",
            "failure_acknowledgement_uncertain",
            "execution",
            "input_assembly",
            "group_execution",
        ],
        exc: Exception,
    ) -> None:
        log.error(
            "daily_forecast.failure",
            group_id=str(group_id),
            attempt_id=str(attempt_id) if attempt_id is not None else None,
            phase=phase,
            error_type=type(exc).__name__,
            failure_reason=_failure_reason(exc).value,
            ownership_uncertain=(
                attempt_id in self.uncertain or phase == "admission_uncertain"
            ),
        )
        self.errors.append(
            f"Daily group {group_id} attempt {attempt_id} {phase}: {type(exc).__name__}"
        )

    def _admission_error(
        self,
        key: DailyClaimKey,
        attempt_id: UUID | None,
        exc: Exception,
    ) -> None:
        # A request UUID is diagnostic, not proof of claim ownership.
        if attempt_id is not None and attempt_id in self.pending:
            self.uncertain.add(attempt_id)
        self.record_failure(
            group_id=key.group_id,
            attempt_id=attempt_id,
            phase=(
                "admission_refused"
                if attempt_id not in self.pending
                and isinstance(exc, (ValueError, ConfigurationError))
                else "admission_uncertain"
            ),
            exc=exc,
        )

    def fail_claim(self, claim: DailyExecution, reason: DailyFailureReason) -> None:
        if claim.id in self.uncertain:
            return
        self.uncertain.add(claim.id)
        self.store.fail(claim, reason=reason, finished_at=self.clock())
        self.uncertain.remove(claim.id)
        self.pending.pop(claim.id, None)

    def claim_scheduled(self) -> None:
        scheduled = current_scheduled_daily_run(
            expected_deployment_id=self.settings.prefect_deployment_id
        )
        if scheduled is None:
            return
        unavailable: set[StationGroupId] = set()

        def preparation_error(gid: StationGroupId, exc: Exception) -> None:
            if not isinstance(exc, ConfigurationError):
                unavailable.add(gid)
            self.record_failure(
                group_id=gid, attempt_id=None, phase="preparation", exc=exc
            )

        candidates = prepare_daily_candidates(
            settings=self.settings,
            context=scheduled.context,
            requirements=self.model.data_requirements,
            group_store=self.groups,
            artifact_store=self.artifacts,
            configuration_sha256=self.configuration_sha256,
            on_error=preparation_error,
        )
        claim_daily_candidates(
            tenant_id=self.settings.tenant_id,
            deployment=self.settings.deployment,
            context=scheduled.context,
            candidates=candidates,
            unavailable_group_ids=frozenset(unavailable),
            store=self.store,
            owner_id=scheduled.owner_id,
            clock=self.clock,
            id_gen=self.id_gen,
            on_claim=lambda claim: self.pending.__setitem__(claim.id, claim),
            on_closed=self._forget_claim,
            on_error=self._admission_error,
        )

    def _forget_claim(self, claim: DailyClaim) -> None:
        self.pending.pop(claim.id, None)

    def handles(self, group_id: StationGroupId, model_id: ModelId) -> bool:
        return (
            group_id in self.settings.group_ids and model_id == self.settings.model_id
        )

    def fail_unstarted(self, reason: DailyFailureReason) -> None:
        for claim in tuple(self.pending.values()):
            if claim.id in self.uncertain:
                continue
            try:
                self.fail_claim(claim, reason)
            except Exception as exc:
                self.record_failure(
                    group_id=claim.key.group_id,
                    attempt_id=claim.id,
                    phase="failure_acknowledgement_uncertain",
                    exc=exc,
                )

    def execute(
        self,
        *,
        operational_ids: frozenset[StationId],
        forecast_bindings: Mapping[StationId, StationWeatherSource],
        forcing_source: WeatherReanalysisSource,
        weather_forecast_store: WeatherForecastStore,
        obs_store: ObservationStore,
        station_store: StationStore,
        basin_store: BasinStore,
        model_state_store: ModelStateStore,
        cycle_time: UtcDatetime,
        nwp_cycle_source: NwpCycleSource,
        nwp_cycle_reference_time: UtcDatetime | None,
        qc_checker: ForecastOutputQualityChecker,
        qc_rules: ForecastQcRuleSet,
        all_baselines: Mapping[StationId, list[ClimBaseline]],
        water_level_datums_masl: dict[StationId, float | None],
        rng: random.Random,
    ) -> tuple[int, tuple[str, ...]]:
        stored_count = 0
        errors: list[str] = []
        for claim in tuple(self.pending.values()):
            if claim.id in self.uncertain:
                continue
            try:
                count, claim_errors = self._execute_claim(
                    claim=claim,
                    operational_ids=operational_ids,
                    forecast_bindings=forecast_bindings,
                    forcing_source=forcing_source,
                    weather_forecast_store=weather_forecast_store,
                    obs_store=obs_store,
                    station_store=station_store,
                    basin_store=basin_store,
                    model_state_store=model_state_store,
                    cycle_time=cycle_time,
                    nwp_cycle_source=nwp_cycle_source,
                    nwp_cycle_reference_time=nwp_cycle_reference_time,
                    qc_checker=qc_checker,
                    qc_rules=qc_rules,
                    all_baselines=all_baselines,
                    water_level_datums_masl=water_level_datums_masl,
                    rng=rng,
                )
                stored_count += count
                errors.extend(claim_errors)
            except Exception as exc:
                if isinstance(exc, DailyOutputRolledBackError):
                    self.uncertain.discard(claim.id)
                self.record_failure(
                    group_id=claim.key.group_id,
                    attempt_id=claim.id,
                    phase="execution",
                    exc=exc,
                )
                if claim.id in self.pending and claim.id not in self.uncertain:
                    try:
                        self.fail_claim(claim, _failure_reason(exc))
                    except Exception as close_exc:
                        self.record_failure(
                            group_id=claim.key.group_id,
                            attempt_id=claim.id,
                            phase="failure_acknowledgement_uncertain",
                            exc=close_exc,
                        )
                if not isinstance(claim, DailyClaim) and claim.id in self.pending:
                    raise
        return stored_count, tuple(self.errors + errors)

    def _execute_claim(
        self,
        *,
        claim: DailyExecution,
        operational_ids: frozenset[StationId],
        forecast_bindings: Mapping[StationId, StationWeatherSource],
        forcing_source: WeatherReanalysisSource,
        weather_forecast_store: WeatherForecastStore,
        obs_store: ObservationStore,
        station_store: StationStore,
        basin_store: BasinStore,
        model_state_store: ModelStateStore,
        cycle_time: UtcDatetime,
        nwp_cycle_source: NwpCycleSource,
        nwp_cycle_reference_time: UtcDatetime | None,
        qc_checker: ForecastOutputQualityChecker,
        qc_rules: ForecastQcRuleSet,
        all_baselines: Mapping[StationId, list[ClimBaseline]],
        water_level_datums_masl: dict[StationId, float | None],
        rng: random.Random,
    ) -> tuple[int, tuple[str, ...]]:
        errors: list[str] = []
        try:
            current = prepare_daily_assignments(
                settings=replace(
                    self.settings, group_ids=frozenset({claim.key.group_id})
                ),
                requirements=self.model.data_requirements,
                group_store=self.groups,
                artifact_store=self.artifacts,
                configuration_sha256=self.configuration_sha256,
            ).get(claim.key.group_id)
        except ConfigurationError:
            self.fail_claim(claim, DailyFailureReason.CONFIGURATION_CHANGED)
            return 0, tuple(errors)
        if (
            current is None
            or current.artifact_id != claim.frozen.artifact_id
            or current.configuration_sha256 != claim.frozen.configuration_sha256
            or current.output_contracts != claim.frozen.output_contracts
        ):
            self.fail_claim(claim, DailyFailureReason.CONFIGURATION_CHANGED)
            return 0, tuple(errors)
        group = self.groups.fetch_group(claim.key.group_id)
        if group is None:
            self.fail_claim(claim, DailyFailureReason.CONFIGURATION_CHANGED)
            return 0, tuple(errors)
        assignment = min(
            (
                a
                for a in self.groups.fetch_group_model_assignments(group.id)
                if a.model_id == claim.key.model_id
                and a.status is ModelAssignmentStatus.ACTIVE
            ),
            key=lambda a: (a.priority, a.time_step),
            default=None,
        )
        if assignment is None:
            self.fail_claim(claim, DailyFailureReason.CONFIGURATION_CHANGED)
            return 0, tuple(errors)
        exclusions = tuple(
            replace(
                member,
                state=DailyMemberState.CONFIGURATION_EXCLUDED,
                reason=DailyMemberReason.ASSIGNMENT_CHANGED
                if member.station_id not in group.station_ids
                else DailyMemberReason.NOT_OPERATIONAL,
            )
            for member in claim.frozen.members
            if member.station_id not in group.station_ids
            or member.station_id not in operational_ids
        )
        member_ids = frozenset(
            m.station_id
            for m in claim.frozen.members
            if m.station_id in group.station_ids and m.station_id in operational_ids
        )
        restricted = replace(group, station_ids=member_ids)

        def assemble(
            selected_cycle: UtcDatetime,
            *,
            restricted: StationGroup = restricted,
            claim: DailyExecution = claim,
            member_ids: frozenset[StationId] = member_ids,
            assignment: GroupModelAssignment = assignment,
        ) -> GroupInputAssembly:
            return assemble_group_operational_inputs_outcome(
                group=restricted,
                model=self.model,
                model_id=claim.key.model_id,
                issue_time=claim.key.context.issue_time,
                cycle_time=selected_cycle,
                nwp_source_by_station={
                    sid: forecast_bindings[sid].nwp_source
                    for sid in member_ids
                    if sid in forecast_bindings
                },
                forcing_source=forcing_source,
                weather_forecast_store=weather_forecast_store,
                obs_store=obs_store,
                station_store=station_store,
                basin_store=basin_store,
                model_state_store=model_state_store,
                clock=self.clock,
                forecast_horizon_steps=self.model.data_requirements.forecast_horizon_steps,
                time_step=assignment.time_step,
                weather_policy=FutureWeatherPolicy.COMPLETE_DAILY,
            )

        try:
            if isinstance(claim, DailyClaim):
                selected = assemble_scheduled_daily_weather(
                    assemble=assemble,
                    store=weather_forecast_store,
                    station_ids=member_ids,
                    sources=frozenset(
                        forecast_bindings[sid].nwp_source
                        for sid in member_ids
                        if sid in forecast_bindings
                    ),
                    future_features=self.model.data_requirements.future_dynamic_features,
                    cycle_time=cycle_time,
                    cycle_source=nwp_cycle_source,
                    reference_time=nwp_cycle_reference_time,
                    issue_time=claim.key.context.issue_time,
                    now=self.clock(),
                    max_age_hours=self.config.nwp_max_fallback_age_hours,
                )
            else:
                selected = DailyWeatherAssembly(
                    assembly=assemble(cycle_time),
                    source=nwp_cycle_source,
                    reference_time=nwp_cycle_reference_time,
                )
            assembly = selected.assembly
        except Exception as exc:
            self.record_failure(
                group_id=group.id, attempt_id=claim.id, phase="input_assembly", exc=exc
            )
            reason = (
                _failure_reason(exc)
                if isinstance(
                    exc, (ConfigurationError, UnsupportedModelRequirementError)
                )
                else DailyFailureReason.SHARED_INPUT_FAILURE
            )
            self.fail_claim(claim, reason)
            errors.append(f"Daily input failure for {group.id}: {type(exc).__name__}")
            return 0, tuple(errors)
        outcome = GroupForecastOutcome(
            results={}, unavailable_members=assembly.unavailable_members
        )
        try:
            if assembly.inputs is not None:
                outcome = run_group_forecast(
                    group=restricted,
                    group_inputs=assembly.inputs,
                    metadata_by_station=assembly.metadata_by_station,
                    assignment=assignment,
                    model=self.model,
                    artifact_store=self.artifacts,
                    qc_checker=qc_checker,
                    qc_rules=qc_rules,
                    qc_overrides=[],
                    baselines_by_station={
                        sid: all_baselines.get(sid, []) for sid in member_ids
                    },
                    nwp_cycle_reference_time=selected.reference_time,
                    nwp_cycle_source=selected.source,
                    config=self.config,
                    clock=self.clock,
                    id_gen=self.id_gen,
                    rng=rng,
                    water_level_datums_masl=water_level_datums_masl,
                    unavailable_members=assembly.unavailable_members,
                    frozen_artifact_id=claim.frozen.artifact_id,
                    state_policy=GroupStatePolicy.STATELESS_ONLY,
                )
                if outcome.failure is not None:
                    raise outcome.failure
        except Exception as exc:
            self.record_failure(
                group_id=group.id, attempt_id=claim.id, phase="group_execution", exc=exc
            )
            if isinstance(exc, GroupForecastError) and exc.rejected:
                failed = GroupForecastOutcome(results={}, rejected=exc.rejected)
                provenance = capture_daily_group_provenance(
                    result=failed,
                    metadata_by_station=assembly.metadata_by_station,
                    nwp_cycle_source=selected.source,
                    nwp_cycle_reference_time=selected.reference_time,
                )
                # Validate the entire diagnostic batch before any write.
                if len({p.station_id for p in exc.rejected}) != len(exc.rejected):
                    raise ModelOutputError(
                        "duplicate carried rejection member"
                    ) from exc
                for payload in exc.rejected:
                    validate_daily_rejection(
                        claim, payload, provenance[payload.station_id]
                    )
                self.uncertain.add(claim.id)
                for committed_count, payload in enumerate(exc.rejected):
                    try:
                        commit_daily_rejection(
                            claim,
                            payload,
                            provenance[payload.station_id],
                            self.rejected_writer,
                        )
                    except DailyOutputRolledBackError as capture_exc:
                        if committed_count:
                            raise StoreError(
                                "earlier diagnostic evidence committed"
                            ) from capture_exc
                        raise
                self.uncertain.remove(claim.id)
            self.fail_claim(claim, _failure_reason(exc))
            return 0, tuple(errors)
        captured = capture_daily_group_provenance(
            result=outcome,
            metadata_by_station=assembly.metadata_by_station,
            nwp_cycle_source=selected.source,
            nwp_cycle_reference_time=selected.reference_time,
        )
        validate_daily_group(
            claim=claim, result=outcome, exclusions=exclusions, provenance=captured
        )
        # A write/commit can now be uncertain. Never release this owner on error.
        self.uncertain.add(claim.id)
        completed = complete_daily_group(
            claim=claim,
            result=outcome,
            exclusions=exclusions,
            provenance=captured,
            forecast_writer=self.writer,
            rejected_writer=self.rejected_writer,
            store=self.store,
            clock=self.clock,
        )
        self.uncertain.remove(claim.id)
        self.pending.pop(claim.id, None)
        stored_count = sum(
            ref.source is DailyOutputSource.FORECAST
            for member in completed.outcomes
            for ref in member.references
        )
        return stored_count, tuple(errors)


def reserves_daily_group(
    *,
    config: DeploymentConfig,
    group: StationGroup,
    model_id: ModelId,
    group_store: StationGroupStore,
) -> bool:
    if config.daily_evaluation is None:
        return False
    settings = config.daily_evaluation.to_domain()
    if model_id != settings.model_id:
        return False
    if group.id in settings.group_ids:
        return True
    configured = [group_store.fetch_group(gid) for gid in settings.group_ids]
    if any(item is None for item in configured):
        raise ConfigurationError("cannot resolve daily reserved roster")
    return any(
        item is not None and bool(item.station_ids & group.station_ids)
        for item in configured
    )


def prepare_daily_execution(
    *,
    config: DeploymentConfig,
    models: Mapping[ModelId, ForecastModel],
    group_store: StationGroupStore | None,
    artifact_store: ModelArtifactStore,
    store: DailyRuntimeStore | None,
    writer: CommittedForecastWriter | None,
    rejected_writer: CommittedRejectedForecastWriter | None,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
) -> DailyForecastExecution | None:
    if config.daily_evaluation is None:
        return None
    if (
        group_store is None
        or store is None
        or writer is None
        or rejected_writer is None
    ):
        raise ConfigurationError(
            "daily evaluation requires transactional stores and groups"
        )
    settings = config.daily_evaluation.to_domain()
    model = models.get(settings.model_id)
    if (
        model is None
        or getattr(model, "artifact_scope", None) is not ArtifactScope.GROUP
    ):
        raise ConfigurationError("daily evaluation requires the configured group model")
    execution = DailyForecastExecution(
        config=config,
        settings=settings,
        model=cast("GroupForecastModel", model),
        group_store=group_store,
        artifact_store=artifact_store,
        store=store,
        writer=writer,
        rejected_writer=rejected_writer,
        clock=clock,
        id_gen=id_gen,
    )
    try:
        execution.claim_scheduled()
    except Exception as exc:
        for group_id in settings.group_ids:
            execution.record_failure(
                group_id=group_id, attempt_id=None, phase="preparation", exc=exc
            )
        execution.fail_unstarted(_failure_reason(exc))
    return execution
