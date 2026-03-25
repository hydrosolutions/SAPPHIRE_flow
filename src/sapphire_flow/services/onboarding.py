from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog

from sapphire_flow.services.baselines import compute_clim_baselines
from sapphire_flow.services.flow_regime import compute_flow_regime
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import aggregate_qc_status
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.onboarding import OnboardingResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.protocols.stores import (
        BasinStore,
        ClimBaselineStore,
        FlowRegimeConfigStore,
        HistoricalForcingStore,
        ObservationStore,
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
    for station in stations:
        try:
            existing = station_store.fetch_station_by_code(
                station.code, station.network
            )
            if existing is not None:
                stations_skipped += 1
                station_map[station.code] = existing.id
                log.info("station_already_exists", code=station.code)
            else:
                station_store.store_station(station)
                station_map[station.code] = station.id
                stations_created += 1
                log.info("station_stored", code=station.code)
        except Exception as exc:
            msg = f"Failed to store station {station.code}: {exc}"
            log.error("station_store_error", code=station.code, error=str(exc))
            errors.append(msg)

    # Resolved StationId set + per-station target parameter for downstream steps
    resolved_station_ids = set(station_map.values())

    # Build lookup: station_id → forecast_target parameter for QC/baseline/regime
    station_target: dict[StationId, str] = {}
    for station in stations:
        sid = station_map.get(station.code)
        if sid is not None and station.forecast_target is not None:
            station_target[sid] = station.forecast_target

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
        parameter = station_target.get(station_id, "discharge")
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

    # Step 6: Compute climatological baselines (per station's target parameter)
    for station_id in resolved_station_ids:
        parameter = station_target.get(station_id, "discharge")
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

    # Step 7: Compute flow regimes (per station's target parameter)
    for station_id in resolved_station_ids:
        parameter = station_target.get(station_id, "discharge")
        try:
            qc_passed = obs_store.fetch_observations(
                station_id,
                parameter,
                start_utc,
                end_utc,
                qc_status=QcStatus.QC_PASSED,
            )
            regime = compute_flow_regime(
                qc_passed, station_id, parameter, clock, uuid4
            )
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
    )

    log.info(
        "onboarding_complete",
        stations_created=result.stations_created,
        stations_skipped=result.stations_skipped,
        observations_imported=result.observations_imported,
        errors=len(result.errors),
    )
    return result
