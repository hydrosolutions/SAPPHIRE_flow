from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.daily_forecast_execution import DailyForecastExecution
from sapphire_flow.flows.daily_forecast_one_shot_runtime import (
    LocalOneShotOutputs,
    LocalOutputState,
)
from sapphire_flow.services.daily_forecast_candidates import prepare_daily_assignments
from sapphire_flow.types.daily_forecast import OneShotDailyForecast
from sapphire_flow.types.daily_forecast_claim import DailyFailureReason
from sapphire_flow.types.daily_forecast_one_shot import OneShotDailyKey
from sapphire_flow.types.enums import ArtifactScope

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Mapping
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
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ClimBaseline, ForecastQcRuleSet
    from sapphire_flow.types.enums import NwpCycleSource
    from sapphire_flow.types.ids import StationGroupId, StationId
    from sapphire_flow.types.station import StationWeatherSource


@dataclass(frozen=True, kw_only=True, slots=True)
class OneShotGroupInputs:
    """Already staged inputs; weather cycle and execution clock are not issue time."""

    operational_ids: frozenset[StationId]
    forecast_bindings: Mapping[StationId, StationWeatherSource]
    forcing_source: WeatherReanalysisSource
    weather_forecast_store: WeatherForecastStore
    obs_store: ObservationStore
    station_store: StationStore
    basin_store: BasinStore
    model_state_store: ModelStateStore
    cycle_time: UtcDatetime
    nwp_cycle_source: NwpCycleSource
    nwp_cycle_reference_time: UtcDatetime | None
    qc_checker: ForecastOutputQualityChecker
    qc_rules: ForecastQcRuleSet
    all_baselines: Mapping[StationId, list[ClimBaseline]]
    water_level_datums_masl: dict[StationId, float | None]
    rng: random.Random


def run_local_one_shot_group(
    *,
    outputs: LocalOneShotOutputs,
    context: OneShotDailyForecast,
    execution_id: UUID,
    owner_id: UUID,
    group_id: StationGroupId,
    config: DeploymentConfig,
    model: GroupForecastModel,
    group_store: StationGroupStore,
    artifact_store: ModelArtifactStore,
    inputs: OneShotGroupInputs,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], UUID],
) -> tuple[int, tuple[str, ...]]:
    """Execute one configured assignment once, only into owned local outputs."""
    if (
        type(outputs) is not LocalOneShotOutputs
        or outputs.state is not LocalOutputState.OPEN
    ):
        raise ValueError("one-shot requires an open owned local output runtime")
    if not isinstance(context, OneShotDailyForecast):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("one-shot requires an explicit typed date and trigger")
    if model.artifact_scope is not ArtifactScope.GROUP:
        raise ConfigurationError("one-shot requires the configured group model")
    if config.daily_evaluation is None:
        raise ConfigurationError("one-shot requires configured daily assignments")
    settings = config.daily_evaluation.to_domain()
    if group_id not in settings.group_ids:
        raise ConfigurationError("one-shot group is not configured")
    # Freeze every configured assignment first: selection cannot hide overlap.
    execution = DailyForecastExecution(
        config=config,
        settings=settings,
        model=model,
        group_store=group_store,
        artifact_store=artifact_store,
        store=outputs.ledger,
        writer=outputs.accepted_writer,
        rejected_writer=outputs.rejected,
        clock=clock,
        id_gen=id_gen,
    )
    frozen = prepare_daily_assignments(
        settings=settings,
        requirements=model.data_requirements,
        group_store=group_store,
        artifact_store=artifact_store,
        configuration_sha256=execution.configuration_sha256,
    ).get(group_id)
    if frozen is None:
        raise ConfigurationError("one-shot group has no active configured assignment")
    key = OneShotDailyKey(
        tenant_id=settings.tenant_id,
        deployment=settings.deployment,
        group_id=group_id,
        model_id=settings.model_id,
        context=context,
    )
    claim = outputs.ledger.start_one_shot(
        key, frozen, execution_id=execution_id, owner_id=owner_id, started_at=clock()
    )
    execution.pending = {claim.id: claim}
    try:
        if frozen.artifact_id is None:
            execution.fail_claim(claim, DailyFailureReason.CONFIGURATION_UNAVAILABLE)
            return 0, ("one-shot requires exactly one active daily artifact",)
        return execution.execute(
            operational_ids=inputs.operational_ids,
            forecast_bindings=inputs.forecast_bindings,
            forcing_source=inputs.forcing_source,
            weather_forecast_store=inputs.weather_forecast_store,
            obs_store=inputs.obs_store,
            station_store=inputs.station_store,
            basin_store=inputs.basin_store,
            model_state_store=inputs.model_state_store,
            cycle_time=inputs.cycle_time,
            nwp_cycle_source=inputs.nwp_cycle_source,
            nwp_cycle_reference_time=inputs.nwp_cycle_reference_time,
            qc_checker=inputs.qc_checker,
            qc_rules=inputs.qc_rules,
            all_baselines=inputs.all_baselines,
            water_level_datums_masl=inputs.water_level_datums_masl,
            rng=inputs.rng,
        )
    finally:
        # The shared executor retains uncertain owners without reissuing a
        # transition. Unknown output/summary commits must remain ACTIVE.
        execution.fail_unstarted(DailyFailureReason.CONFIGURATION_UNAVAILABLE)
