from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import structlog
from prefect import flow, task

from sapphire_flow.services.onboarding import onboard_from_camelsch
from sapphire_flow.types.datetime import ensure_utc

log = structlog.get_logger(__name__)


@task(name="download-camels-ch")
def _download_task(data_dir: str) -> str:
    import camelsch

    dest = Path(data_dir)
    log.info("download_starting", dest=str(dest))
    result = camelsch.download_camels_ch(dest=dest)
    log.info("download_complete", data_dir=str(result))
    return str(result)


def _load_qc_rules() -> object:
    from sapphire_flow.config.qc_rules import load_qc_rules

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        return load_qc_rules(config_path)
    from sapphire_flow.config.qc_rules import _default_swiss_qc_rules

    return _default_swiss_qc_rules()


def _resolve_default_camels_dir() -> str:
    from sapphire_flow.config.paths import resolve_data_dir

    config_data_dir: str | None = None
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        from sapphire_flow.config.deployment import load_config

        config_data_dir = load_config(config_path).paths_data_dir
    return str(resolve_data_dir(config_data_dir) / "raw" / "CAMELS_CH")


@flow(name="onboard-stations", log_prints=False)
def onboard_stations_flow(
    data_dir: str = "",
    basin_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    download: bool = False,
    basin_store: object = None,
    station_store: object = None,
    obs_store: object = None,
    forcing_store: object = None,
    baseline_store: object = None,
    flow_regime_store: object = None,
    model_store: object = None,
    artifact_store: object = None,
    group_store: object = None,
    hindcast_store: object = None,
    skill_store: object = None,
    forcing_source: object = None,
    deployment_config: object = None,
    qc_rules: object = None,
    clock: object = None,
    hindcast_days: int | None = None,
) -> object:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    data_dir = data_dir or _resolve_default_camels_dir()

    if download:
        data_dir = _download_task(data_dir)

    _conn: object = None
    if basin_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        basin_store = stores["basin_store"]
        station_store = stores["station_store"]
        obs_store = stores["obs_store"]
        forcing_store = stores["forcing_store"]
        baseline_store = stores["baseline_store"]
        flow_regime_store = stores["flow_regime_store"]
        model_store = stores["model_store"]
        artifact_store = stores["artifact_store"]
        group_store = stores["group_store"]
        hindcast_store = stores["hindcast_store"]
        skill_store = stores["skill_store"]

    # Build store-backed forcing source for training (CAMELS-CH data)
    if forcing_source is None and forcing_store is not None:
        from sapphire_flow.adapters.store_backed_reanalysis import (
            StoreBackedReanalysisSource,
        )

        forcing_source = StoreBackedReanalysisSource(forcing_store)

    # Load deployment config for skill gate thresholds (production path only)
    if deployment_config is None and forcing_source is not None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            deployment_config = load_config(config_path)

    # Read basin_ids from config if not provided via argument
    if basin_ids is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.onboarding import load_onboarding_config

            onboarding_cfg = load_onboarding_config(config_path)
            if onboarding_cfg is not None:
                basin_ids = list(onboarding_cfg.basin_ids)
                log.info("basin_ids_from_config", count=len(basin_ids))

    if qc_rules is None:
        qc_rules = _load_qc_rules()

    log.info(
        "onboarding_starting",
        data_dir=data_dir,
        basin_ids=basin_ids,
        start_date=start_date,
        end_date=end_date,
    )

    result = onboard_from_camelsch(
        data_dir=Path(data_dir),
        basin_store=basin_store,
        station_store=station_store,
        obs_store=obs_store,
        forcing_store=forcing_store,
        baseline_store=baseline_store,
        flow_regime_store=flow_regime_store,
        qc_rules=qc_rules,
        clock=clock,
        basin_ids=basin_ids,
        start_date=start_date,
        end_date=end_date,
        model_store=model_store,
        artifact_store=artifact_store,
        group_store=group_store,
        hindcast_store=hindcast_store,
        skill_store=skill_store,
        forcing_source=forcing_source,
        deployment_config=deployment_config,
        hindcast_days=hindcast_days,
    )

    log.info(
        "onboarding_flow_complete",
        stations_created=result.stations_created,
        stations_skipped=result.stations_skipped,
        stations_updated=result.stations_updated,
        basins_created=result.basins_created,
        basins_skipped=result.basins_skipped,
        observations_imported=result.observations_imported,
        forcing_records_imported=result.forcing_records_imported,
        observations_qc_passed=result.observations_qc_passed,
        observations_qc_failed=result.observations_qc_failed,
        observations_qc_suspect=result.observations_qc_suspect,
        baselines_computed=result.baselines_computed,
        flow_regimes_computed=result.flow_regimes_computed,
        model_assignments_created=result.model_assignments_created,
        models_trained=result.models_trained,
        stations_marked_operational=result.stations_marked_operational,
        errors=len(result.errors),
    )

    return result
