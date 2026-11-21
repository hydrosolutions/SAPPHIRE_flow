"""Preview or submit one audited daily retry through the trusted operations plane."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from prefect.client.orchestration import get_client

from sapphire_flow.config.deployment import load_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.daily_forecast_execution import execution_configuration_sha256
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.daily_forecast_candidates import prepare_daily_assignments
from sapphire_flow.store.daily_forecast_store import (
    PgDailyForecastStore,
    daily_forecast_transaction_factory,
    parse_operator_request,
)
from sapphire_flow.types.daily_forecast import DailyAttemptOrdinal, DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import DailyClaim, DailyClaimKey
from sapphire_flow.types.daily_operator_retry import (
    OperatorDailyForecast,
    OperatorRetryRequest,
    roster_sha256,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import PrincipalId, StationGroupId

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.protocols.forecast_model import GroupForecastModel
    from sapphire_flow.protocols.stores import ModelArtifactStore, StationGroupStore
    from sapphire_flow.services.daily_forecast_preflight import DailyRuntimeStore
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)


def preview_retry(
    *,
    config: DeploymentConfig,
    group_id: StationGroupId,
    groups: StationGroupStore,
    artifacts: ModelArtifactStore,
    daily: DailyRuntimeStore,
    model: GroupForecastModel,
    actor: PrincipalId,
    reason: str,
    request_id: UUID,
    now: UtcDatetime,
) -> OperatorRetryRequest:
    if config.daily_evaluation is None:
        raise ConfigurationError("daily evaluation is not configured")
    settings = config.daily_evaluation.to_domain()
    if group_id not in settings.group_ids:
        raise ConfigurationError("group is outside configured daily scope")
    candidates = prepare_daily_assignments(
        settings=settings,
        requirements=model.data_requirements,
        group_store=groups,
        artifact_store=artifacts,
        configuration_sha256=execution_configuration_sha256(config),
    )
    frozen = candidates.get(group_id)
    if frozen is None or frozen.artifact_id is None:
        raise ConfigurationError(
            "daily assignment or unique active artifact is unavailable"
        )
    key = DailyClaimKey(
        tenant_id=settings.tenant_id,
        deployment=settings.deployment,
        group_id=group_id,
        model_id=settings.model_id,
        context=OperatorDailyForecast(target_date=now.date()),
    )
    latest = daily.read_day(key).latest
    if latest is None or latest.state not in (
        DailyAttemptState.FAILED,
        DailyAttemptState.INCOMPLETE,
        DailyAttemptState.ABANDONED,
    ):
        raise ConfigurationError("daily retry requires a terminal unsuccessful parent")
    if (
        isinstance(latest.claim, DailyClaim)
        and latest.claim.ordinal is not DailyAttemptOrdinal.RETRY
    ):
        raise ConfigurationError("automatic attempts are not exhausted")
    previous = latest.claim.frozen
    if (
        previous.artifact_id != frozen.artifact_id
        or roster_sha256(previous) != roster_sha256(frozen)
        or previous.output_contracts != frozen.output_contracts
    ):
        raise ConfigurationError(
            "operator retry cannot change artifact, roster or output contract"
        )
    log.info(
        "daily_operator_retry.preview",
        parent_id=str(latest.claim.id),
        parent_configuration_sha256=previous.configuration_sha256,
        configuration_sha256=frozen.configuration_sha256,
    )
    record = artifacts.fetch_artifact_record(frozen.artifact_id)
    if record is None:
        raise ConfigurationError("artifact identity is unavailable")
    return OperatorRetryRequest(
        request_id=request_id,
        parent_id=latest.claim.id,
        key=key,
        artifact_id=frozen.artifact_id,
        artifact_sha256=record.sha256_hash,
        configuration_sha256=frozen.configuration_sha256,
        roster_sha256=roster_sha256(frozen),
        actor=actor,
        reason=reason,
    )


async def submit_retry(request: OperatorRetryRequest, config: DeploymentConfig) -> UUID:
    if config.daily_evaluation is None:
        raise ConfigurationError("daily evaluation is not configured")
    settings = config.daily_evaluation.to_domain()
    if (
        request.key.tenant_id != settings.tenant_id
        or request.key.deployment != settings.deployment
        or request.key.group_id not in settings.group_ids
        or request.key.model_id != settings.model_id
    ):
        raise ConfigurationError("request is outside configured daily scope")
    async with get_client() as client:
        deployment = await client.read_deployment(settings.prefect_deployment_id)
        parameters = dict(deployment.parameters)
        if (
            parameters.get("cycle_time") is not None
            or parameters.get("data_use", "standard") != "standard"
        ):
            raise ConfigurationError(
                "deployment parameters conflict with operator retry"
            )
        parameters["operator_daily_retry"] = request.document()
        run = await client.create_flow_run_from_deployment(
            settings.prefect_deployment_id,
            parameters=parameters,
            idempotency_key=f"operator-daily-retry:{request.request_id}",
        )
    if run.parameters.get("operator_daily_retry") != request.document():
        raise ConfigurationError(
            "request UUID already submitted with different payload"
        )
    return run.id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("SAPPHIRE_CONFIG"))
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview")
    preview.add_argument("--group", required=True, type=UUID)
    preview.add_argument("--actor", required=True)
    preview.add_argument("--reason", required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--request-file", required=True, type=Path)
    args = parser.parse_args()
    configure_cli_logging()
    try:
        if not args.config:
            raise ConfigurationError("--config or SAPPHIRE_CONFIG is required")
        config = load_config(args.config)
        if args.command == "submit":
            if args.request_file.stat().st_size > 16384:
                raise ValueError("operator request exceeds 16 KiB")
            request = parse_operator_request(json.loads(args.request_file.read_text()))
            run_id = asyncio.run(submit_retry(request, config))
            sys.stdout.write(
                json.dumps(
                    {"flow_run_id": str(run_id), "request_id": str(request.request_id)}
                )
                + "\n"
            )
            return 0
        from sapphire_flow.config.paths import resolve_artifact_dir
        from sapphire_flow.services.model_registry import discover_models
        from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
        from sapphire_flow.store.station_group_store import PgStationGroupStore

        if config.daily_evaluation is None:
            raise ConfigurationError("daily evaluation is not configured")
        model = discover_models().get(config.daily_evaluation.to_domain().model_id)
        if model is None:
            raise ConfigurationError("configured model is unavailable")
        engine = create_engine_from_env()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("SET TRANSACTION READ ONLY"))
                request = preview_retry(
                    config=config,
                    group_id=StationGroupId(args.group),
                    groups=PgStationGroupStore(conn),
                    artifacts=PgModelArtifactStore(conn, resolve_artifact_dir()),
                    daily=PgDailyForecastStore(
                        daily_forecast_transaction_factory(engine.url)
                    ),
                    model=cast("GroupForecastModel", model),
                    actor=PrincipalId(args.actor.strip()),
                    reason=args.reason.strip(),
                    request_id=uuid4(),
                    now=ensure_utc(datetime.now(UTC)),
                )
        finally:
            engine.dispose()
        sys.stdout.write(json.dumps(request.document(), indent=2) + "\n")
        return 0
    except (ValueError, OSError, ConfigurationError) as exc:
        log.error("daily_operator_retry.refused", reason=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
