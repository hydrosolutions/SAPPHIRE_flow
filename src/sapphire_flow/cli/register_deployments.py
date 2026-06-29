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
FLOW_SOURCE_ROOT = "/app"


@dataclass(frozen=True, slots=True)
class DeploymentSpec:
    flow_module: str
    flow_attr: str
    deployment_name: str
    cron: str | None = None
    concurrency_limit: int | None = None


def _build_specs() -> list[DeploymentSpec]:
    """Build deployment specs with env-var-configurable schedules."""
    cron_ingest = os.environ.get("SCHEDULE_INGEST_OBSERVATIONS", "*/5 * * * *")
    cron_forecast = os.environ.get("SCHEDULE_FORECAST_CYCLE", "0 */6 * * *")
    cron_backup = os.environ.get("SCHEDULE_BACKUP_DATABASE", "0 2 * * *")

    return [
        DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_observations",
            flow_attr="ingest_observations_flow",
            deployment_name="ingest-observations",
            cron=cron_ingest,
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
        "work_pool_name": WORK_POOL,
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

    async with get_client() as client:
        try:
            await client.create_work_pool(
                WorkPoolCreate(name=WORK_POOL, type="process")
            )
            log.info("workpool.created", name=WORK_POOL)
        except ObjectAlreadyExists:
            log.info("workpool.exists", name=WORK_POOL)

    specs = _build_specs()
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
