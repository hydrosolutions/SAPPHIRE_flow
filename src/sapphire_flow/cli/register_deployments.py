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
from typing import TYPE_CHECKING, cast

import structlog

from sapphire_flow.config.river_stations import ForecastConfigOverlay

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def forecast_job_variables(
    existing: object, overlay: ForecastConfigOverlay | None
) -> dict[str, object]:
    if not isinstance(existing, dict) or any(
        not isinstance(key, str) for key in cast("dict[object, object]", existing)
    ):
        raise ValueError("forecast job variables must be a string-keyed mapping")
    result = dict(cast("dict[str, object]", existing))
    raw_env = result.get("env", {})
    if not isinstance(raw_env, dict) or any(
        not isinstance(key, str) for key in cast("dict[object, object]", raw_env)
    ):
        raise ValueError("forecast job environment must be a string-keyed mapping")
    environment = dict(cast("dict[str, object]", raw_env))
    if overlay is None:
        environment.pop("SAPPHIRE_FORECAST_CONFIG_OVERLAY", None)
    else:
        environment["SAPPHIRE_FORECAST_CONFIG_OVERLAY"] = overlay.environment_value()
    if "env" in result or environment:
        result["env"] = environment
    return result


@dataclass(frozen=True, slots=True)
class DeploymentSpec:
    flow_module: str
    flow_attr: str
    deployment_name: str
    cron: str | None = None
    concurrency_limit: int | None = None
    work_pool_name: str = WORK_POOL
    forecast_config_overlay: ForecastConfigOverlay | None = None
    parameters: tuple[tuple[str, str], ...] = ()


_SKIP_ENV_VAR = "SAPPHIRE_SKIP_DEPLOYMENTS"
_ONLY_ENV_VAR = "SAPPHIRE_ONLY_DEPLOYMENTS"
_SOURCE_ROOT_ENV_VAR = "SAPPHIRE_FLOW_SOURCE_ROOT"


def _era5_refresh_specs(env: Mapping[str, str]) -> list[DeploymentSpec]:
    cron = env.get("SCHEDULE_REFRESH_OPERATIONAL_ERA5", "").strip()
    scope = env.get("SAPPHIRE_ERA5_WEATHER_SCOPE", "").strip()
    if not cron and not scope:
        return []
    if not cron or not scope:
        raise ValueError("ERA5 refresh requires both schedule and weather scope")
    from prefect.client.schemas.schedules import CronSchedule

    CronSchedule(cron=cron, timezone="UTC")
    return [
        DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_recap_era5_reanalysis",
            flow_attr="refresh_operational_era5_history_flow",
            deployment_name="refresh-operational-era5-history",
            cron=cron,
            concurrency_limit=1,
            work_pool_name=INGEST_POOL,
            parameters=(("weather_scope", scope),),
        )
    ]


def _flow_source_root() -> str:
    return os.environ.get(_SOURCE_ROOT_ENV_VAR) or FLOW_SOURCE_ROOT


def _only_deployments(
    environment: Mapping[str, str] | None = None,
) -> frozenset[str]:
    raw = (os.environ if environment is None else environment).get(_ONLY_ENV_VAR, "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def _skipped_deployments(
    environment: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Deployment names a host opts out of (Plan 511 D4), comma-separated."""
    raw = (os.environ if environment is None else environment).get(_SKIP_ENV_VAR, "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def _build_specs(environment: Mapping[str, str] | None = None) -> list[DeploymentSpec]:
    """Build the deployment specs, minus any the host opts out of."""
    specs = _all_specs(environment)
    skipped = _skipped_deployments(environment)
    only = _only_deployments(environment)
    if skipped and only:
        raise ValueError(f"{_SKIP_ENV_VAR} and {_ONLY_ENV_VAR} cannot both be set")
    known = {spec.deployment_name for spec in specs}
    for env_var, names in ((_SKIP_ENV_VAR, skipped), (_ONLY_ENV_VAR, only)):
        if names - known:
            raise ValueError(
                f"{env_var} names unknown deployments: {sorted(names - known)}"
            )
    if only:
        if environment is None:
            log.info("deployments.subset", only=sorted(only))
        return [spec for spec in specs if spec.deployment_name in only]
    if (
        environment is None
        and "forecast-cycle" in skipped
        and any(spec.forecast_config_overlay is not None for spec in specs)
    ):
        log.info("deployment.forecast_config_unused", reason="deployment_skipped")
    return [spec for spec in specs if spec.deployment_name not in skipped]


def build_deployment_specs(
    environment: Mapping[str, str],
) -> tuple[DeploymentSpec, ...]:
    """Preview registrar specifications from explicit inputs without registration."""
    return tuple(_build_specs(environment))


def _all_specs(environment: Mapping[str, str] | None = None) -> list[DeploymentSpec]:
    """Build deployment specs with env-var-configurable schedules."""
    env = os.environ if environment is None else environment
    forecast_overlay = ForecastConfigOverlay.parse(
        env.get("SAPPHIRE_FORECAST_CONFIG_OVERLAY")
    )
    cron_ingest = env.get("SCHEDULE_INGEST_OBSERVATIONS", "*/5 * * * *")
    cron_forecast = env.get("SCHEDULE_FORECAST_CYCLE", "0 */6 * * *")
    cron_backup = env.get("SCHEDULE_BACKUP_DATABASE", "0 2 * * *")
    cron_weather_history = env.get("SCHEDULE_INGEST_WEATHER_HISTORY", "0 6 * * *")
    # Plan 146 D2: daily rolling ingest for the recap-Gateway snow-reanalysis
    # channel — a distinct time from ingest-weather-history so both never
    # contend for the DB at the same moment.
    cron_snow_reanalysis = env.get("SCHEDULE_INGEST_SNOW_REANALYSIS", "0 5 * * *")
    # Plan 111 route-C BAFU forecast collector. Hourly matches BAFU's issue
    # rhythm; a run is ~70s and dedups on issued_at, so hourly never overlaps
    # and re-fetches are cheap no-ops. Retune via the env var without a redeploy.
    cron_bafu_forecast = env.get("SCHEDULE_COLLECT_BAFU_FORECASTS", "0 * * * *")
    # Plan 136 BAFU LINDAS observation archive collector. Plan 176 D1:
    # LINDAS actually publishes on a 10-minute grid (the "hourly refresh"
    # claim that justified the old `37 * * * *` was measured and falsified
    # — see docs/plans/archive/176-lindas-archive-completeness.md § Evidence).
    # Over-poll at roughly 2.5x the grid rather than match it exactly — a
    # poll rate equal to the grid rate loses a slot whenever publish jitter
    # puts two slots between two polls (D1's "why not simply poll every 10
    # minutes" note).
    #
    # Plan 189 T2: tightened after Plan 176 T7's 45-min live run measured a
    # 7.0 min MINIMUM publish gap (D1's original bound was sized on a single
    # 10.8 min sample — a 2.7x margin that was really 1.75x). LOCKED
    # PROPERTIES (not this literal string — a test asserts them): max
    # CYCLIC inter-poll gap <=3 min (incl. the `:58 -> :01` wrap; 2.33x
    # margin on the measured 7.0 min minimum), min gap >=2 min, every
    # minute non-divisible by 5 (never shares a minute with
    # ingest-observations' `*/5` tick or the BAFU-forecast collector's
    # `0 * * * *` tick). The min gap was RELAXED from D1's original >=3 —
    # `max<=3` AND `min>=3` together force every gap to exactly 3 (a single
    # residue class mod 3), and every mod-3 residue class contains 4
    # multiples of 5, which is arithmetically incompatible with "no minute
    # divisible by 5". At a 2 min minimum the gap can equal Plan 175's
    # 120s total retry deadline under sustained upstream failure; accepted
    # because a healthy run takes ~0.1s, concurrency_limit=1 serialises
    # rather than overlaps runs, and a feed failing that long trips the D4
    # freshness alert regardless (see docs/plans/archive/189-audit-window-edge-and-
    # poll-bound.md § T2).
    cron_bafu_observation = env.get(
        "SCHEDULE_COLLECT_BAFU_OBSERVATIONS",
        "1,3,6,8,11,13,16,18,21,23,26,28,31,33,36,38,41,43,46,48,51,53,56,58 * * * *",
    )

    return [
        *_era5_refresh_specs(env),
        DeploymentSpec(
            flow_module="sapphire_flow.flows.ingest_recap_era5_reanalysis",
            flow_attr="ingest_operational_era5_history_flow",
            deployment_name="ingest-operational-era5-history",
            concurrency_limit=1,
            work_pool_name=INGEST_POOL,
        ),
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
            forecast_config_overlay=forecast_overlay,
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
            # Plan 176 D5/T1: routed onto the nearly-idle `ingest` pool
            # (its own dedicated worker/event loop) rather than `default` —
            # insurance against the worker-side poll-cycle starvation Plan
            # 098 measured (25-60 min) during forecast-cycle windows on the
            # shared pool. See docs/plans/archive/176-lindas-archive-completeness.md
            # § Execution isolation.
            work_pool_name=INGEST_POOL,
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
        source=_flow_source_root(),
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
        if spec.deployment_name == "refresh-operational-era5-history":
            from prefect.schedules import Cron

            deploy_kwargs["schedule"] = Cron(spec.cron, timezone="UTC")
        else:
            deploy_kwargs["cron"] = spec.cron
    if spec.concurrency_limit is not None:
        deploy_kwargs["concurrency_limit"] = spec.concurrency_limit

    if spec.deployment_name in {
        "forecast-cycle",
        "backup-database",
        "refresh-operational-era5-history",
    }:
        from prefect.client.orchestration import get_client
        from prefect.exceptions import ObjectNotFound

        async with get_client() as client:
            try:
                existing = await client.read_deployment_by_name(
                    f"{flow_fn.name}/{spec.deployment_name}"
                )
            except ObjectNotFound:
                variables: object = {}
                parameters: dict[str, object] = dict(spec.parameters)
            else:
                variables = existing.job_variables
                parameters = dict(existing.parameters)
                if (
                    spec.deployment_name == "refresh-operational-era5-history"
                    and parameters.get("weather_scope")
                    != dict(spec.parameters)["weather_scope"]
                ):
                    raise ValueError(
                        "Existing ERA5 refresh scope differs from requested scope"
                    )
        deploy_kwargs["parameters"] = parameters
        if spec.deployment_name == "forecast-cycle":
            deploy_kwargs["job_variables"] = forecast_job_variables(
                variables, spec.forecast_config_overlay
            )

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
