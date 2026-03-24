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


def _setup_production_stores(
    database_url: str,
) -> tuple[object, dict[str, object]]:
    import sqlalchemy as sa

    from sapphire_flow.flows._db import make_pg_stores, run_migrations

    engine = sa.create_engine(database_url)
    log.info("migrations_running")
    run_migrations(engine)
    log.info("migrations_complete")
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    stores = make_pg_stores(conn)
    return conn, stores


@flow(name="onboard-stations", log_prints=False)
def onboard_stations_flow(
    data_dir: str = "./data/CAMELS_CH",
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
    qc_rules: object = None,
    clock: object = None,
) -> object:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    if download:
        data_dir = _download_task(data_dir)

    _conn: object = None
    if basin_store is None:
        database_url = os.environ["DATABASE_URL"]
        _conn, stores = _setup_production_stores(database_url)
        basin_store = stores["basin_store"]
        station_store = stores["station_store"]
        obs_store = stores["obs_store"]
        forcing_store = stores["forcing_store"]
        baseline_store = stores["baseline_store"]
        flow_regime_store = stores["flow_regime_store"]

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
    )

    log.info(
        "onboarding_flow_complete",
        stations_created=result.stations_created,
        stations_skipped=result.stations_skipped,
        basins_created=result.basins_created,
        basins_skipped=result.basins_skipped,
        observations_imported=result.observations_imported,
        forcing_records_imported=result.forcing_records_imported,
        observations_qc_passed=result.observations_qc_passed,
        observations_qc_failed=result.observations_qc_failed,
        observations_qc_suspect=result.observations_qc_suspect,
        baselines_computed=result.baselines_computed,
        flow_regimes_computed=result.flow_regimes_computed,
        errors=len(result.errors),
    )

    return result
