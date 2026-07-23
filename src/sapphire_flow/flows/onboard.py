from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from prefect import flow, runtime, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.services.onboarding import onboard_from_camelsch
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.stores import BasinStore, StationStore
    from sapphire_flow.services.reanalysis_backfill import MeteoSwissBackfillAdapter
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)


def _resolve_onboard_stations_flow_run_name() -> str:
    scheduled = getattr(runtime.flow_run, "scheduled_start_time", None)
    if scheduled is None:
        return "onboard-stations"
    try:
        return f"onboard-stations-{scheduled:%Y-%m-%dT%H%M}"
    except (TypeError, ValueError):
        return "onboard-stations"


@task(
    name="download-camels-ch",
    task_run_name="download-camels-ch",
    cache_policy=NO_CACHE,
)
def _download_task(data_dir: str) -> str:
    """Download CAMELS-CH dataset to ``data_dir``.

    NOTE: The dev compose overlay (``docker-compose.dev.yml``) bind-mounts
    ``/data/raw`` read-only from ``CAMELS_CH_HOST_DIR``, so ``download=True`` is
    incompatible with the dev overlay — pre-stage the dataset host-side via the
    env var. Production/staging overlays must likewise provide a writable
    ``/data/raw`` if this task is to run.
    """
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


@flow(
    name="onboard-stations",
    log_prints=False,
    flow_run_name=_resolve_onboard_stations_flow_run_name,
)
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
    parameter_store: object = None,
    formula_store: object = None,
    forcing_source: object = None,
    deployment_config: object = None,
    qc_rules: object = None,
    clock: object = None,
    hindcast_days: int | None = None,
    reanalysis_adapter_factory: object = None,
    require_meteoswiss_backfill: bool = False,
    calculated_specs: object = None,
    lineage_writer: object = None,
    tenant_store: object = None,
    tenant_code: str | None = None,
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
        parameter_store = stores["parameter_store"]
        formula_store = stores.get("formula_store")
        if tenant_store is None:
            tenant_store = stores.get("tenant_store")
        if lineage_writer is None:
            lineage_writer = stores.get("lineage_writer")

        # Plan 115b2 §2B/§2C: the MeteoSwiss reanalysis binding + per-station
        # backfill-or-hold, wired ONLY on this production DB-backed path (not
        # when a caller injects its own stores, e.g. tests/replay) — so the
        # §2C hold gate is unconditionally live for the real deployed flow,
        # matching how forcing_source/deployment_config are resolved below.
        #
        # A FACTORY (not a pre-built adapter) is threaded so onboarding can
        # build the adapter AFTER it has persisted the basins/stations — the
        # production adapter snapshots its per-station basin map at
        # construction, so an earlier build would miss a genuinely new
        # station's basin. ``require_meteoswiss_backfill`` is set True here so
        # eligible stations are held out of promotion whether or not the fetch
        # produces rows.
        if reanalysis_adapter_factory is None:
            from typing import cast

            from sapphire_flow.flows.ingest_weather_history import (
                _load_reanalysis_stac_config,  # pyright: ignore[reportPrivateUsage]
                build_production_reanalysis_adapter,
            )

            _stac_config = _load_reanalysis_stac_config()
            _station_store = cast("StationStore", station_store)
            _basin_store = cast("BasinStore", basin_store)
            _clock = cast("Callable[[], UtcDatetime]", clock)

            def _build_reanalysis_adapter() -> MeteoSwissBackfillAdapter:
                return build_production_reanalysis_adapter(
                    config=_stac_config,
                    station_store=_station_store,
                    basin_store=_basin_store,
                    clock=_clock,
                )

            reanalysis_adapter_factory = _build_reanalysis_adapter
            require_meteoswiss_backfill = True

    # Load deployment config for skill gate thresholds and to select the
    # reanalysis-source mode below (production path only).
    if deployment_config is None and forcing_store is not None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            deployment_config = load_config(config_path)
        else:
            from sapphire_flow.config.deployment import DeploymentConfig

            deployment_config = DeploymentConfig(max_retention_days=600)

    # Route through the single reanalysis-source factory (Plan 115a §6) so the
    # mode is a deployment decision made in exactly one place —
    # DeploymentConfig.reanalysis_source (Plan 115b4 §5D default "hybrid").
    if forcing_source is None and forcing_store is not None:
        from typing import cast

        from sapphire_flow.adapters.hybrid_reanalysis_factories import (
            select_reanalysis_source,
        )
        from sapphire_flow.config.deployment import DeploymentConfig

        resolved_config = cast("DeploymentConfig", deployment_config)
        forcing_source = select_reanalysis_source(
            forcing_store=forcing_store, mode=resolved_config.reanalysis_source
        )

    # Read basin_ids from config if not provided via argument
    water_level_datums_masl: dict[str, float] | None = None
    water_level_units: dict[str, str] | None = None
    if basin_ids is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.onboarding import load_onboarding_config

            onboarding_cfg = load_onboarding_config(config_path)
            if onboarding_cfg is not None:
                basin_ids = list(onboarding_cfg.basin_ids)
                water_level_datums_masl = onboarding_cfg.water_level_datums_masl
                water_level_units = onboarding_cfg.water_level_units
                if calculated_specs is None:
                    calculated_specs = onboarding_cfg.calculated
                if tenant_code is None:
                    tenant_code = onboarding_cfg.tenant_code
                log.info("basin_ids_from_config", count=len(basin_ids))
    else:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.onboarding import load_onboarding_config

            onboarding_cfg = load_onboarding_config(config_path)
            if onboarding_cfg is not None:
                water_level_datums_masl = onboarding_cfg.water_level_datums_masl
                water_level_units = onboarding_cfg.water_level_units
                if calculated_specs is None:
                    calculated_specs = onboarding_cfg.calculated
                if tenant_code is None:
                    tenant_code = onboarding_cfg.tenant_code

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
        parameter_store=parameter_store,  # type: ignore[arg-type]
        water_level_datums_masl=water_level_datums_masl,
        water_level_units=water_level_units,
        reanalysis_adapter_factory=reanalysis_adapter_factory,  # type: ignore[arg-type]
        require_meteoswiss_backfill=require_meteoswiss_backfill,
        formula_store=formula_store,  # type: ignore[arg-type]
        calculated_specs=calculated_specs or (),  # type: ignore[arg-type]
        lineage_writer=lineage_writer,
        tenant_store=tenant_store,  # type: ignore[arg-type]
        tenant_code=tenant_code or DEFAULT_TENANT_CODE,
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
