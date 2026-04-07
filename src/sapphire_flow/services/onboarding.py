from __future__ import annotations

import random as _random
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from sapphire_flow.services.baselines import compute_clim_baselines
from sapphire_flow.services.flow_regime import compute_flow_regime
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import aggregate_qc_status
from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelArtifactStatus,
    QcStatus,
    StationKind,
    StationStatus,
)
from sapphire_flow.types.onboarding import OnboardingResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ClimBaselineStore,
        FlowRegimeConfigStore,
        HindcastStore,
        HistoricalForcingStore,
        ModelArtifactStore,
        ModelStore,
        ObservationStore,
        SkillStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

_WIDE_START = ensure_utc(datetime(1980, 1, 1, tzinfo=UTC))
_WIDE_END = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))


def _run_onboarding(
    stations: list[StationConfig],
    basins: list[Basin],
    obs_by_station: dict[StationId, list[RawObservation]],
    forcing_by_station: dict[StationId, list[RawHistoricalForcing]],
    basin_store: BasinStore,
    station_store: StationStore,
    obs_store: ObservationStore,
    forcing_store: HistoricalForcingStore,
    baseline_store: ClimBaselineStore,
    flow_regime_store: FlowRegimeConfigStore,
    qc_rules: QcRuleSet,
    clock: Callable[[], UtcDatetime],
    start_utc: UtcDatetime,
    end_utc: UtcDatetime,
    model_store: ModelStore | None = None,
    artifact_store: ModelArtifactStore | None = None,
    group_store: StationGroupStore | None = None,
    hindcast_store: HindcastStore | None = None,
    skill_store: SkillStore | None = None,
    forcing_source: WeatherReanalysisSource | None = None,
    deployment_config: DeploymentConfig | None = None,
) -> OnboardingResult:
    errors: list[str] = []
    stations_created = 0
    stations_skipped = 0
    basins_created = 0
    basins_skipped = 0
    observations_imported = 0
    forcing_records_imported = 0
    observations_qc_passed = 0
    observations_qc_failed = 0
    observations_qc_suspect = 0
    baselines_computed = 0
    flow_regimes_computed = 0
    model_assignments_created = 0
    models_trained = 0
    stations_marked_operational = 0

    # Build basin lookup by code for cross-referencing stations
    basin_code_to_id = {b.code: b.id for b in basins}

    # Step 1: Store basins
    for basin in basins:
        try:
            existing = basin_store.fetch_basin_by_code(basin.code, basin.network)
            if existing is not None:
                basins_skipped += 1
                basin_code_to_id[basin.code] = existing.id
                log.info("basin_already_exists", code=basin.code)
            else:
                basin_store.store_basin(basin)
                basins_created += 1
                log.info("basin_stored", code=basin.code)
        except Exception as exc:
            msg = f"Failed to store basin {basin.code}: {exc}"
            log.error("basin_store_error", code=basin.code, error=str(exc))
            errors.append(msg)

    # Step 2: Store stations; build station_map for downstream
    # Maps the original gauge_id (station.code) → StationId
    station_map: dict[str, StationId] = {}
    # Build lookup: station_id → forecast_targets parameter for QC/baseline/regime
    station_target: dict[StationId, str] = {}
    for station in stations:
        try:
            existing = station_store.fetch_station_by_code(
                station.code, station.network
            )
            if existing is not None:
                stations_skipped += 1
                station_map[station.code] = existing.id
                if existing.forecast_targets:
                    ft = existing.forecast_targets
                    station_target[existing.id] = next(iter(ft), "discharge")
                log.info("station_already_exists", code=station.code)
            else:
                station_store.store_station(station)
                station_map[station.code] = station.id
                stations_created += 1
                if station.forecast_targets:
                    ft = station.forecast_targets
                    station_target[station.id] = next(iter(ft), "discharge")
                log.info("station_stored", code=station.code)
        except Exception as exc:
            msg = f"Failed to store station {station.code}: {exc}"
            log.error("station_store_error", code=station.code, error=str(exc))
            errors.append(msg)

    # Resolved StationId set for downstream steps
    resolved_station_ids = set(station_map.values())

    # Step 3: Store observations
    for station_id, raw_obs in obs_by_station.items():
        if station_id not in resolved_station_ids:
            continue
        try:
            obs_store.store_raw_observations(raw_obs)
            observations_imported += len(raw_obs)
            log.debug(
                "observations_stored",
                station_id=str(station_id),
                count=len(raw_obs),
            )
        except Exception as exc:
            msg = f"Failed to store observations for station {station_id}: {exc}"
            log.error(
                "observations_store_error", station_id=str(station_id), error=str(exc)
            )
            errors.append(msg)

    # Step 4: Store forcing
    for station_id, forcing in forcing_by_station.items():
        if station_id not in resolved_station_ids:
            continue
        try:
            forcing_store.store_forcing(forcing)
            forcing_records_imported += len(forcing)
            log.debug(
                "forcing_stored",
                station_id=str(station_id),
                count=len(forcing),
            )
        except Exception as exc:
            msg = f"Failed to store forcing for station {station_id}: {exc}"
            log.error("forcing_store_error", station_id=str(station_id), error=str(exc))
            errors.append(msg)

    # Step 5: Run QC (per station, using the station's target parameter)
    checker = Stage1QualityChecker()
    for station_id in resolved_station_ids:
        parameter = station_target.get(station_id)
        if parameter is None:
            log.warning("station_no_forecast_targets", station_id=str(station_id))
            continue
        try:
            raw_obs = obs_store.fetch_observations(
                station_id, parameter, start_utc, end_utc, qc_status=QcStatus.RAW
            )
            if not raw_obs:
                continue
            flags = checker.check(raw_obs, qc_rules, overrides=[], baselines=[])
            for obs_id, obs_flags in flags.items():
                status = aggregate_qc_status(obs_flags)
                obs_store.update_qc(obs_id, status, obs_flags)
                if status == QcStatus.QC_PASSED:
                    observations_qc_passed += 1
                elif status == QcStatus.QC_FAILED:
                    observations_qc_failed += 1
                elif status == QcStatus.QC_SUSPECT:
                    observations_qc_suspect += 1
            log.debug(
                "qc_complete",
                station_id=str(station_id),
                parameter=parameter,
                count=len(raw_obs),
            )
        except Exception as exc:
            msg = f"QC failed for station {station_id}: {exc}"
            log.error("qc_error", station_id=str(station_id), error=str(exc))
            errors.append(msg)

    # Step 5b: Compute climatological baselines (per station's target parameter)
    for station_id in resolved_station_ids:
        parameter = station_target.get(station_id)
        if parameter is None:
            log.warning("station_no_forecast_targets", station_id=str(station_id))
            continue
        try:
            qc_passed = obs_store.fetch_observations(
                station_id,
                parameter,
                start_utc,
                end_utc,
                qc_status=QcStatus.QC_PASSED,
            )
            clim = compute_clim_baselines(qc_passed, station_id, parameter)
            if clim:
                baseline_store.store_baselines(clim)
                baselines_computed += len(clim)
                log.debug(
                    "baselines_computed",
                    station_id=str(station_id),
                    parameter=parameter,
                    count=len(clim),
                )
        except Exception as exc:
            msg = f"Baseline computation failed for station {station_id}: {exc}"
            log.error("baseline_error", station_id=str(station_id), error=str(exc))
            errors.append(msg)

    # Step 5c: Compute flow regimes (per station's target parameter)
    for station_id in resolved_station_ids:
        parameter = station_target.get(station_id)
        if parameter is None:
            log.warning("station_no_forecast_targets", station_id=str(station_id))
            continue
        try:
            qc_passed = obs_store.fetch_observations(
                station_id,
                parameter,
                start_utc,
                end_utc,
                qc_status=QcStatus.QC_PASSED,
            )
            regime = compute_flow_regime(qc_passed, station_id, parameter, clock, uuid4)
            if regime is not None:
                flow_regime_store.store_config(regime)
                flow_regimes_computed += 1
                log.debug(
                    "flow_regime_computed",
                    station_id=str(station_id),
                    parameter=parameter,
                )
        except Exception as exc:
            msg = f"Flow regime computation failed for station {station_id}: {exc}"
            log.error("flow_regime_error", station_id=str(station_id), error=str(exc))
            errors.append(msg)

    # Step 6: Configure model assignments
    # Skipped if model infrastructure not wired
    discovered: dict = {}
    if model_store is not None:
        from sapphire_flow.services.model_onboarding import create_station_assignment
        from sapphire_flow.services.model_registry import (
            discover_models,
            register_models,
        )

        discovered = discover_models()
        if discovered:
            register_models(discovered, model_store, clock)
        for station_id in resolved_station_ids:
            station = station_store.fetch_station(station_id)
            if station is None or station.station_kind == StationKind.WEATHER:
                continue
            for model_id, model in discovered.items():
                if model.artifact_scope == ArtifactScope.GROUP:
                    continue
                try:
                    time_step = next(iter(model.data_requirements.supported_time_steps))
                    create_station_assignment(
                        station_id=station_id,
                        model_id=model_id,
                        time_step=time_step,
                        priority=0,
                        station_store=station_store,
                        clock=clock,
                    )
                    model_assignments_created += 1
                except Exception as exc:
                    errors.append(
                        f"Model assignment failed for {station_id}/{model_id}: {exc}"
                    )
                    log.error(
                        "onboarding.assignment_error",
                        station_id=str(station_id),
                        error=str(exc),
                    )

    # Step 7: Trigger training via onboard_model service
    # Skipped if artifact_store or forcing_source is None
    if (
        model_store is not None
        and artifact_store is not None
        and group_store is not None
        and hindcast_store is not None
        and skill_store is not None
        and forcing_source is not None
        and deployment_config is not None
    ):
        from sapphire_flow.services.model_onboarding import (
            determine_onboarding_scope,
            onboard_model,
        )

        if not discovered:
            from sapphire_flow.services.model_registry import discover_models

            discovered = discover_models()

        non_weather_ids = frozenset(
            sid
            for sid in resolved_station_ids
            if (s := station_store.fetch_station(sid)) is not None
            and s.station_kind != StationKind.WEATHER
        )
        for model_id, model in discovered.items():
            if model.artifact_scope == ArtifactScope.GROUP:
                continue
            if not non_weather_ids:
                continue
            try:
                time_step = next(iter(model.data_requirements.supported_time_steps))
                units = determine_onboarding_scope(
                    model_id=model_id,
                    model=model,
                    station_ids=non_weather_ids,
                    group_ids=None,
                    station_store=station_store,
                    group_store=group_store,
                    training_period_start=start_utc,
                    training_period_end=end_utc,
                    time_step=time_step,
                )
                result_mo = onboard_model(
                    model_id=model_id,
                    model=model,
                    units=units,
                    model_store=model_store,
                    station_store=station_store,
                    group_store=group_store,
                    artifact_store=artifact_store,
                    obs_store=obs_store,
                    basin_store=basin_store,
                    hindcast_store=hindcast_store,
                    skill_store=skill_store,
                    flow_regime_store=flow_regime_store,
                    forcing_source=forcing_source,
                    config=deployment_config,
                    clock=clock,
                    rng=_random.Random(42),
                )
                models_trained += result_mo.promoted_count()
            except Exception as exc:
                errors.append(f"Training failed for model {model_id}: {exc}")
                log.error(
                    "onboarding.training_error",
                    model_id=str(model_id),
                    error=str(exc),
                )

    # Step 8: Mark stations operational
    # Weather stations: operational after QC (steps 1-5 complete)
    # Non-weather: operational if ≥1 ACTIVE model artifact exists
    for station_id in resolved_station_ids:
        station = station_store.fetch_station(station_id)
        if station is None:
            continue
        if station.station_kind == StationKind.WEATHER:
            try:
                station_store.update_station_status(
                    station_id, StationStatus.OPERATIONAL
                )
                stations_marked_operational += 1
            except Exception as exc:
                errors.append(
                    f"Failed to mark weather station {station_id} operational: {exc}"
                )
        elif artifact_store is not None:
            has_active = any(
                len(
                    artifact_store.fetch_artifacts_by_status(
                        model_id=mid,
                        status=ModelArtifactStatus.ACTIVE,
                        station_id=station_id,
                    )
                )
                > 0
                for mid in discovered
            )
            if has_active:
                try:
                    station_store.update_station_status(
                        station_id, StationStatus.OPERATIONAL
                    )
                    stations_marked_operational += 1
                    log.info(
                        "onboarding.station_operational", station_id=str(station_id)
                    )
                except Exception as exc:
                    errors.append(
                        f"Failed to mark station {station_id} operational: {exc}"
                    )
            else:
                log.warning(
                    "onboarding.station_no_active_artifact",
                    station_id=str(station_id),
                )

    return OnboardingResult(
        stations_created=stations_created,
        stations_skipped=stations_skipped,
        basins_created=basins_created,
        basins_skipped=basins_skipped,
        observations_imported=observations_imported,
        forcing_records_imported=forcing_records_imported,
        observations_qc_passed=observations_qc_passed,
        observations_qc_failed=observations_qc_failed,
        observations_qc_suspect=observations_qc_suspect,
        baselines_computed=baselines_computed,
        flow_regimes_computed=flow_regimes_computed,
        errors=errors,
        model_assignments_created=model_assignments_created,
        models_trained=models_trained,
        stations_marked_operational=stations_marked_operational,
    )


def onboard_from_camelsch(
    data_dir: Path | str,
    basin_store: BasinStore,
    station_store: StationStore,
    obs_store: ObservationStore,
    forcing_store: HistoricalForcingStore,
    baseline_store: ClimBaselineStore,
    flow_regime_store: FlowRegimeConfigStore,
    qc_rules: QcRuleSet,
    clock: Callable[[], UtcDatetime],
    basin_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    model_store: ModelStore | None = None,
    artifact_store: ModelArtifactStore | None = None,
    group_store: StationGroupStore | None = None,
    hindcast_store: HindcastStore | None = None,
    skill_store: SkillStore | None = None,
    forcing_source: WeatherReanalysisSource | None = None,
    deployment_config: DeploymentConfig | None = None,
) -> OnboardingResult:
    from sapphire_flow.adapters.camelsch_adapter import (
        load_forcing,
        load_observations,
        load_stations,
    )

    start_utc = (
        ensure_utc(datetime.fromisoformat(start_date).replace(tzinfo=UTC))
        if start_date
        else _WIDE_START
    )
    end_utc = (
        ensure_utc(datetime.fromisoformat(end_date).replace(tzinfo=UTC))
        if end_date
        else _WIDE_END
    )

    log.info(
        "onboarding_started",
        data_dir=str(data_dir),
        basin_ids=basin_ids,
        start_date=start_date,
        end_date=end_date,
    )

    stations, basins = load_stations(data_dir, clock, basin_ids)

    station_map: dict[str, StationId] = {s.code: s.id for s in stations}

    obs_by_station = load_observations(
        data_dir, station_map, clock, start_date, end_date
    )
    forcing_by_station = load_forcing(data_dir, station_map, start_date, end_date)

    result = _run_onboarding(
        stations=stations,
        basins=basins,
        obs_by_station=obs_by_station,
        forcing_by_station=forcing_by_station,
        basin_store=basin_store,
        station_store=station_store,
        obs_store=obs_store,
        forcing_store=forcing_store,
        baseline_store=baseline_store,
        flow_regime_store=flow_regime_store,
        qc_rules=qc_rules,
        clock=clock,
        start_utc=start_utc,
        end_utc=end_utc,
        model_store=model_store,
        artifact_store=artifact_store,
        group_store=group_store,
        hindcast_store=hindcast_store,
        skill_store=skill_store,
        forcing_source=forcing_source,
        deployment_config=deployment_config,
    )

    log.info(
        "onboarding_complete",
        stations_created=result.stations_created,
        stations_skipped=result.stations_skipped,
        observations_imported=result.observations_imported,
        errors=len(result.errors),
    )
    return result
