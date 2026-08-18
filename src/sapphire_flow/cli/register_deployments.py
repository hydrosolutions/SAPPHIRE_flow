"""Register Prefect deployments for all v0 flows.

Run via: python -m sapphire_flow.cli.register_deployments

Idempotent — re-running creates or updates existing deployments.
Cron schedules are configurable via environment variables.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

WORK_POOL = "default"
INGEST_POOL = "ingest"
# Plan 162 D2/T2: the dedicated backup component — the only work pool served
# by a worker holding the read-everything `sapphire_backup` credential
# (prefect-worker-backup, docker-compose.yml). Created below (register_all)
# in the same set-comprehension loop as every other pool, which already runs
# BEFORE the deployment-registration loop — required ordering, since `init`
# registers deployments before any worker starts (docker-compose.yml).
BACKUP_POOL = "backup"
FLOW_SOURCE_ROOT = "/app"


@dataclass(frozen=True, slots=True)
class DeploymentSpec:
    flow_module: str
    flow_attr: str
    deployment_name: str
    cron: str | None = None
    concurrency_limit: int | None = None
    work_pool_name: str = WORK_POOL


def _build_specs() -> list[DeploymentSpec]:
    """Build deployment specs with env-var-configurable schedules."""
    cron_ingest = os.environ.get("SCHEDULE_INGEST_OBSERVATIONS", "*/5 * * * *")
    cron_forecast = os.environ.get("SCHEDULE_FORECAST_CYCLE", "0 */6 * * *")
    cron_backup = os.environ.get("SCHEDULE_BACKUP_DATABASE", "0 2 * * *")
    cron_weather_history = os.environ.get(
        "SCHEDULE_INGEST_WEATHER_HISTORY", "0 6 * * *"
    )
    # Plan 146 D2: daily rolling ingest for the recap-Gateway snow-reanalysis
    # channel — a distinct time from ingest-weather-history so both never
    # contend for the DB at the same moment.
    cron_snow_reanalysis = os.environ.get(
        "SCHEDULE_INGEST_SNOW_REANALYSIS", "0 5 * * *"
    )
    # Plan 111 route-C BAFU forecast collector. Hourly matches BAFU's issue
    # rhythm; a run is ~70s and dedups on issued_at, so hourly never overlaps
    # and re-fetches are cheap no-ops. Retune via the env var without a redeploy.
    cron_bafu_forecast = os.environ.get("SCHEDULE_COLLECT_BAFU_FORECASTS", "0 * * * *")
    # Plan 136 BAFU LINDAS observation archive collector. Plan 175 D4/T4:
    # NOT `5 * * * *` — every `*/5` ingest tick lands on minute 5 too,
    # colliding against LINDAS's measured 3-request burst ceiling every
    # hour (see docs/decisions/bafu-lindas-rate-limit.md). `37` is clear of
    # every `*/5` boundary and of the `0 * * * *` forecast collector.
    # NOTE for Plan 176: the "hourly refresh" cadence claim this comment
    # used to justify `:05` is itself falsified — LINDAS actually publishes
    # on a 10-minute grid — so do not re-derive a cadence from this comment.
    cron_bafu_observation = os.environ.get(
        "SCHEDULE_COLLECT_BAFU_OBSERVATIONS", "37 * * * *"
    )

    return [
        DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_observations",
            flow_attr="ingest_observations_flow",
            deployment_name="ingest-observations",
            cron=cron_ingest,
            work_pool_name=INGEST_POOL,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.run_forecast_cycle",
            flow_attr="run_forecast_cycle_flow",
            deployment_name="forecast-cycle",
            cron=cron_forecast,
            concurrency_limit=1,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.backup",
            flow_attr="backup_database_flow",
            deployment_name="backup-database",
            cron=cron_backup,
            # Plan 162 D2: routed to the DEDICATED backup pool, never
            # `default` — the only worker serving this pool holds the
            # read-everything sapphire_backup credential.
            work_pool_name=BACKUP_POOL,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.train_models",
            flow_attr="train_models_flow",
            deployment_name="train-models",
            concurrency_limit=1,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.run_hindcast",
            flow_attr="run_hindcast_flow",
            deployment_name="run-hindcast",
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.compute_skills",
            flow_attr="compute_skills_flow",
            deployment_name="compute-skills",
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.compute_skills",
            flow_attr="compute_combined_skills_flow",
            deployment_name="compute-combined-skills",
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.onboard",
            flow_attr="onboard_stations_flow",
            deployment_name="onboard-stations",
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.onboard_model",
            flow_attr="onboard_model_flow",
            deployment_name="onboard-model",
            concurrency_limit=1,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_weather_history",
            flow_attr="ingest_weather_history_flow",
            deployment_name="ingest-weather-history",
            cron=cron_weather_history,
            concurrency_limit=1,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_recap_reanalysis",
            flow_attr="ingest_recap_reanalysis_flow",
            deployment_name="ingest-recap-reanalysis",
            cron=cron_snow_reanalysis,
            concurrency_limit=1,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.collect_bafu_forecasts",
            flow_attr="collect_bafu_forecasts_flow",
            deployment_name="collect-bafu-forecasts",
            cron=cron_bafu_forecast,
            concurrency_limit=1,
        ),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.collect_bafu_observations",
            flow_attr="collect_bafu_observations_flow",
            deployment_name="collect-bafu-observations",
            cron=cron_bafu_observation,
            concurrency_limit=1,
        ),
        # Plan 157 T3: manually-triggered — no cron; runs on the `default`
        # pool like every other non-ingest deployment. NOTE for whoever
        # builds the aquacast shim (split out of this plan): an import only
        # succeeds where the imported model_id's ENTRY POINT is installed,
        # so a shim-backed model will need this deployment routed to
        # whichever worker carries that distribution. A dedicated
        # forecast-cycle pool was built for that and REVERTED — with the
        # same image on both sides it bought nothing while adding a third
        # pool and a mixed-version upgrade window.
        DeploymentSpec(
            flow_module="sapphire_flow.flows.import_model_artifact",
            flow_attr="import_model_artifact_flow",
            deployment_name="import-model-artifact",
            concurrency_limit=1,
        ),
    ]


async def _register_one(spec: DeploymentSpec) -> None:
    """Register a single deployment using flow.deploy()."""
    import importlib

    module = importlib.import_module(spec.flow_module)
    flow_fn = getattr(module, spec.flow_attr)

    entrypoint = f"src/{spec.flow_module.replace('.', '/')}.py:{spec.flow_attr}"
    sourced_flow = await flow_fn.afrom_source(
        source=FLOW_SOURCE_ROOT,
        entrypoint=entrypoint,
    )

    deploy_kwargs: dict[str, object] = {
        "name": spec.deployment_name,
        "work_pool_name": spec.work_pool_name,
        "build": False,
        "push": False,
        "print_next_steps": False,
    }
    if spec.cron is not None:
        deploy_kwargs["cron"] = spec.cron
    if spec.concurrency_limit is not None:
        deploy_kwargs["concurrency_limit"] = spec.concurrency_limit

    deployment_id = await sourced_flow.adeploy(**deploy_kwargs)
    log.info(
        "deployment.registered",
        name=spec.deployment_name,
        deployment_id=str(deployment_id),
        cron=spec.cron,
        concurrency_limit=spec.concurrency_limit,
    )


async def register_all() -> None:
    """Register all v0 Prefect deployments. Idempotent."""
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.actions import WorkPoolCreate
    from prefect.exceptions import ObjectAlreadyExists

    specs = _build_specs()

    async with get_client() as client:
        for pool_name in {spec.work_pool_name for spec in specs}:
            # Each pool gets its own guard — a single try/except around the loop
            # would abort on the first ObjectAlreadyExists.
            try:
                await client.create_work_pool(
                    WorkPoolCreate(name=pool_name, type="process")
                )
                log.info("workpool.created", name=pool_name)
            except ObjectAlreadyExists:
                log.info("workpool.exists", name=pool_name)

    for spec in specs:
        await _register_one(spec)

    log.info("deployments.complete", count=len(specs))


def main() -> None:
    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()
    asyncio.run(register_all())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("deployment.registration.failed")
        sys.exit(1)
