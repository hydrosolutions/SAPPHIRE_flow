from __future__ import annotations

import random as _random
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.baselines import compute_clim_baselines
from sapphire_flow.services.flow_regime import compute_flow_regime
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.services.qc_datum import (
    SUPPORTED_WATER_LEVEL_UNITS,
    add_observation_datum_details,
    obs_qc_rule_version,
    obs_skipped_rules,
    shift_observations_for_water_level_datum,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import aggregate_qc_status
from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelArtifactStatus,
    QcStatus,
    SpatialRepresentation,
    StationKind,
    StationStatus,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import CLIMATOLOGY_FALLBACK_MODEL_ID
from sapphire_flow.types.onboarding import OnboardingResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ClimBaselineStore,
        FlowRegimeConfigStore,
        HindcastStore,
        HistoricalForcingStore,
        ModelArtifactStore,
        ModelStore,
        ObservationStore,
        ParameterStore,
        SkillStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

_WIDE_START = ensure_utc(datetime(1980, 1, 1, tzinfo=UTC))
_WIDE_END = ensure_utc(datetime(2030, 1, 1, tzinfo=UTC))


def _make_hindcast_fn():  # type: ignore[no-untyped-def]
    """Build the hindcast callback for onboard_model()."""
    from uuid import uuid4

    from sapphire_flow.services.hindcast import run_station_hindcast

    def _run_hindcast(
        *,
        unit,
        model,
        artifact_id,
        artifact_store,
        obs_store,
        hindcast_store,
        forcing_source,
        station_store,
        basin_store,
        clock,
        rng,
    ):  # type: ignore[no-untyped-def]
        result = artifact_store.fetch_artifact(artifact_id)
        if result is None:
            return []
        _, artifact_bytes = result
        artifact = model.deserialize_artifact(artifact_bytes)
        return run_station_hindcast(
            model=model,
            artifact=artifact,
            station_id=unit.station_id,
            model_id=unit.model_id,
            artifact_id=artifact_id,
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            time_step=unit.time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=clock,
            rng=rng,
            hindcast_run_id=uuid4(),
        )

    return _run_hindcast


def _make_skill_fn():  # type: ignore[no-untyped-def]
    """Build the skill computation callback for onboard_model()."""
    from uuid import uuid4

    from sapphire_flow.services.skill.service import compute_skill_for_station
    from sapphire_flow.types.enums import ForcingType

    def _compute_skill(
        *,
        unit,
        model_id,
        artifact_id,
        hindcast_store,
        obs_store,
        skill_store,
        flow_regime_store,
        config,
    ):  # type: ignore[no-untyped-def]
        station_id = unit.station_id
        if station_id is None:
            return  # group-scoped: skip for now

        # Fetch hindcasts for this station/model over training period
        hindcasts = hindcast_store.fetch_hindcasts(
            station_id=station_id,
            model_id=model_id,
            start=unit.training_period_start,
            end=unit.training_period_end,
        )
        if not hindcasts:
            return

        # Fetch observations for the training period
        from sapphire_flow.types.enums import QcStatus

        observations = obs_store.fetch_observations(
            station_id=station_id,
            parameter="discharge",
            start=unit.training_period_start,
            end=unit.training_period_end,
            qc_status=QcStatus.QC_PASSED,
        )

        # Fetch thresholds and flow regime
        thresholds = []  # no thresholds in v0 onboarding
        flow_regime = flow_regime_store.fetch_latest(
            station_id=station_id,
            parameter="discharge",
        )

        from sapphire_flow.types.enums import SkillSource

        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=hindcasts,
            observations=observations,
            thresholds=thresholds,
            flow_regime_config=flow_regime,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=lambda: ensure_utc(datetime.now(UTC)),
            uuid_factory=uuid4,
            parameter="discharge",
        )
        if scores:
            skill_store.store_skill_scores(scores)
        if diagrams:
            skill_store.store_skill_diagrams(diagrams)

    return _compute_skill


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
    hindcast_days: int | None = None,
    parameter_store: ParameterStore | None = None,
) -> OnboardingResult:
    errors: list[str] = []
    stations_created = 0
    stations_skipped = 0
    stations_updated = 0
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
    station_by_id: dict[StationId, StationConfig] = {}
    for station in stations:
        try:
            if (
                station.forecast_targets is not None
                and "water_level" in station.forecast_targets
                and station.water_level_unit not in SUPPORTED_WATER_LEVEL_UNITS
            ):
                raise ConfigurationError(
                    "water_level_unit must be one of "
                    f"{sorted(SUPPORTED_WATER_LEVEL_UNITS)}, got "
                    f"{station.water_level_unit!r} for station {station.code}"
                )
            existing = station_store.fetch_station_by_code(
                station.code, station.network
            )
            if existing is not None:
                station_to_store = replace(station, id=existing.id)
                station_map[station.code] = existing.id
                station_by_id[existing.id] = station_to_store
                if station_to_store.forecast_targets:
                    ft = station_to_store.forecast_targets
                    station_target[existing.id] = next(iter(ft), "discharge")
                t0 = time.perf_counter()
                station_store.update_station(station_to_store)
                duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                stations_updated += 1
                log.info(
                    "station.metadata_updated",
                    station_id=str(existing.id),
                    code=station.code,
                    network=station.network,
                    duration_ms=duration_ms,
                )
            else:
                station_store.store_station(station)
                station_map[station.code] = station.id
                station_by_id[station.id] = station
                stations_created += 1
                if station.forecast_targets:
                    ft = station.forecast_targets
                    station_target[station.id] = next(iter(ft), "discharge")
                log.info("station_stored", code=station.code)
        except ConfigurationError:
            raise
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
            inserted_ids = obs_store.store_raw_observations(raw_obs)
            observations_imported += len(inserted_ids)
            skipped = len(raw_obs) - len(inserted_ids)
            if skipped:
                log.debug(
                    "observation.duplicate_skipped",
                    station_id=str(station_id),
                    skipped_count=skipped,
                )
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

    # Step 4b: Create weather source mappings for stations with forcing data
    for station_id, forcing in forcing_by_station.items():
        if station_id not in resolved_station_ids or not forcing:
            continue
        try:
            from sapphire_flow.types.station import StationWeatherSource

            source_name = forcing[0].source  # e.g. "camels-ch"
            ws = StationWeatherSource(
                station_id=station_id,
                nwp_source=source_name,
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
            )
            station_store.store_weather_source(ws)

            # M3: bind non-weather river stations to the operational ICON forcing
            # path (icon_ch2_eps / BASIN_AVERAGE) alongside the camels-ch / POINT
            # reanalysis binding. Weather stations are forcing SOURCES, not
            # forecast targets, so they get no ICON binding.
            station = station_store.fetch_station(station_id)
            if station is not None and station.station_kind != StationKind.WEATHER:
                station_store.store_weather_source(
                    StationWeatherSource(
                        station_id=station_id,
                        nwp_source="icon_ch2_eps",
                        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                        status=WeatherSourceStatus.ACTIVE,
                    )
                )
        except Exception as exc:
            log.warning(
                "weather_source_store_error",
                station_id=str(station_id),
                error=str(exc),
            )

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
            station = station_by_id.get(station_id)
            datum = (
                station.water_level_datum_masl
                if station is not None and parameter == "water_level"
                else None
            )
            qc_obs = shift_observations_for_water_level_datum(
                raw_obs,
                parameter=parameter,
                datum=datum,
            )
            flags = checker.check(
                qc_obs,
                qc_rules,
                overrides=[],
                baselines=[],
                skipped_rule_ids=obs_skipped_rules(parameter, datum),
            )
            flags = add_observation_datum_details(
                flags,
                raw_observations=raw_obs,
                shifted_observations=qc_obs,
                parameter=parameter,
                datum=datum,
            )
            qc_rule_version = obs_qc_rule_version(parameter, datum)
            for obs_id, obs_flags in flags.items():
                status = aggregate_qc_status(obs_flags)
                obs_store.update_qc(
                    obs_id,
                    status,
                    obs_flags,
                    qc_rule_version=qc_rule_version,
                )
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
            station = station_by_id.get(station_id)
            datum = (
                station.water_level_datum_masl
                if station is not None and parameter == "water_level"
                else None
            )
            if parameter == "water_level" and datum is None:
                log.info(
                    "baselines_skipped_missing_water_level_datum",
                    station_id=str(station_id),
                    parameter=parameter,
                )
                continue
            baseline_obs = shift_observations_for_water_level_datum(
                qc_passed,
                parameter=parameter,
                datum=datum,
            )
            clim = compute_clim_baselines(baseline_obs, station_id, parameter)
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
    discovered: dict[ModelId, ForecastModel] = {}
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
            from sapphire_flow.config.deployment import DEFAULT_PRIORITY
            from sapphire_flow.types.ids import (
                FALLBACK_ASSIGNMENT_PRIORITIES,
                FALLBACK_MODEL_IDS,
            )

            for model_id, model in discovered.items():
                if model.artifact_scope == ArtifactScope.GROUP:
                    continue
                try:
                    time_step = next(iter(model.data_requirements.supported_time_steps))
                    if deployment_config is not None:
                        priority = deployment_config.assignment_priority_for_model(
                            model_id
                        )
                    elif model_id in FALLBACK_MODEL_IDS:
                        priority = FALLBACK_ASSIGNMENT_PRIORITIES[model_id]
                    else:
                        priority = DEFAULT_PRIORITY
                    create_station_assignment(
                        station_id=station_id,
                        model_id=model_id,
                        time_step=time_step,
                        priority=priority,
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
        from sapphire_flow.config.deployment import DEFAULT_PRIORITY
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
            all_have_active = all(
                artifact_store.fetch_active_artifact_for_station(sid, model_id)
                is not None
                for sid in non_weather_ids
            )
            if all_have_active:
                log.debug(
                    "onboarding.training_skipped",
                    model_id=str(model_id),
                    reason="all_stations_have_active_artifact",
                )
                continue
            try:
                if hindcast_days is not None and hindcast_days < 1:
                    raise ValueError(f"hindcast_days must be >= 1, got {hindcast_days}")

                if hindcast_days is not None:
                    from datetime import timedelta

                    hindcast_start = ensure_utc(
                        max(start_utc, end_utc - timedelta(days=hindcast_days))
                    )
                else:
                    hindcast_start = start_utc

                narrowed = hindcast_start > start_utc
                if narrowed:
                    log.warning(
                        "hindcast.period_narrowed",
                        hindcast_start=str(hindcast_start),
                        hindcast_end=str(end_utc),
                        hindcast_days=(end_utc - hindcast_start).days,
                        note="model trains AND evaluates on narrowed window — "
                        "skill scores not comparable to full-period training",
                    )
                elif hindcast_days is not None:
                    log.info(
                        "hindcast.period_unchanged",
                        hindcast_start=str(hindcast_start),
                        hindcast_end=str(end_utc),
                        hindcast_days_requested=hindcast_days,
                        actual_days=(end_utc - start_utc).days,
                        note="hindcast_days exceeds data range — full period",
                    )
                else:
                    log.info(
                        "hindcast.period_resolved",
                        hindcast_start=str(hindcast_start),
                        hindcast_end=str(end_utc),
                        hindcast_days=(end_utc - hindcast_start).days,
                    )

                time_step = next(iter(model.data_requirements.supported_time_steps))
                units = determine_onboarding_scope(
                    model_id=model_id,
                    model=model,
                    station_ids=non_weather_ids,
                    group_ids=None,
                    station_store=station_store,
                    group_store=group_store,
                    training_period_start=hindcast_start,
                    training_period_end=end_utc,
                    time_step=time_step,
                )
                # Resolve the SAME config-driven priority Step 6 uses, so the
                # assignment written on artifact promotion does not regress to the
                # default 0 (Plan 089). The Step 7 guard ensures deployment_config
                # is not None; DEFAULT_PRIORITY is a belt-and-suspenders fallback.
                if deployment_config is not None:
                    priority = deployment_config.assignment_priority_for_model(model_id)
                elif model_id in FALLBACK_MODEL_IDS:
                    priority = FALLBACK_ASSIGNMENT_PRIORITIES[model_id]
                else:
                    priority = DEFAULT_PRIORITY
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
                    assignment_priority=priority,
                    skip_smoke_test=True,
                    run_hindcast_fn=_make_hindcast_fn(),
                    compute_skill_fn=_make_skill_fn(),
                    parameter_store=parameter_store,
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
    # Weather stations: operational after QC (steps 1-5 complete).
    # Non-weather stations are promoted only once the guaranteed floor exists.
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
            active_floor = artifact_store.fetch_artifacts_by_status(
                model_id=CLIMATOLOGY_FALLBACK_MODEL_ID,
                status=ModelArtifactStatus.ACTIVE,
                station_id=station_id,
            )
            if active_floor:
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
                errors.append(
                    "Cannot mark station "
                    f"{station_id} operational: missing active "
                    f"{CLIMATOLOGY_FALLBACK_MODEL_ID} floor artifact"
                )
                log.warning(
                    "onboarding.station_missing_climatology_floor",
                    station_id=str(station_id),
                    model_id=str(CLIMATOLOGY_FALLBACK_MODEL_ID),
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
        stations_updated=stations_updated,
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
    hindcast_days: int | None = None,
    parameter_store: ParameterStore | None = None,
    water_level_datums_masl: dict[str, float] | None = None,
    water_level_units: dict[str, str] | None = None,
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

    stations, basins = load_stations(
        data_dir,
        clock,
        basin_ids,
        water_level_datums_masl=water_level_datums_masl,
        water_level_units=water_level_units,
    )

    station_map: dict[str, StationId] = {}
    for s in stations:
        existing = station_store.fetch_station_by_code(s.code, s.network)
        station_map[s.code] = existing.id if existing is not None else s.id

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
        hindcast_days=hindcast_days,
        parameter_store=parameter_store,
    )

    log.info(
        "onboarding_complete",
        stations_created=result.stations_created,
        stations_skipped=result.stations_skipped,
        stations_updated=result.stations_updated,
        observations_imported=result.observations_imported,
        errors=len(result.errors),
    )
    return result
