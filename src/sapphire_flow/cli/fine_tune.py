"""Preview or start a configured fine-tuning run through Prefect."""

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

import structlog
from prefect.client.orchestration import get_client
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.fine_tuning import load_fine_tuning, parse_fine_tuning
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.logging import configure_cli_logging

if TYPE_CHECKING:
    from prefect.client.orchestration import PrefectClient

    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ModelArtifactStore,
        StationGroupStore,
        StationStore,
    )
    from sapphire_flow.types.fine_tuning import FineTuningRequest

log = structlog.get_logger(__name__)


async def submit_fine_tuning(
    request: FineTuningRequest,
    client: PrefectClient,
    *,
    candidate_id: UUID | None = None,
) -> UUID:
    deployment = await client.read_deployment_by_name(request.deployment_name)
    flow = await client.read_flow(deployment.flow_id)
    if flow.name != "train-models":
        raise ConfigurationError("fine-tuning requires a train-models deployment")
    schema = deployment.parameter_openapi_schema or {}
    if "fine_tuning" not in schema.get("properties", {}):
        raise ConfigurationError(
            "deployment does not support configured fine-tuning; update it first"
        )
    parameters: dict[str, object] = {
        "fine_tuning": request.payload(),
        **dict.fromkeys(
            (
                "model_ids",
                "station_ids",
                "group_ids",
                "period_start",
                "period_end",
                "training_params",
                "base_artifact_id",
                "fine_tuning_candidate_id",
            )
        ),
    }
    key = f"fine-tuning:{request.request_id}"
    if candidate_id is not None:
        parameters["fine_tuning_candidate_id"] = str(candidate_id)
        key = f"fine-tuning-evaluate:{request.request_id}:{candidate_id}:{uuid4()}"
    run = await client.create_flow_run_from_deployment(
        deployment.id,
        parameters=parameters,
        idempotency_key=key,
    )
    original = parse_fine_tuning(run.parameters.get("fine_tuning"))
    if original.sha256 != request.sha256:
        raise ConfigurationError(
            "request UUID already submitted with different configuration"
        )
    return run.id


def review_candidate(args: argparse.Namespace) -> int:
    from sapphire_flow.config.deployment_identity import load_deployment_identity_config
    from sapphire_flow.db.engine import create_engine_from_env
    from sapphire_flow.services.fine_tuning_review import (
        ReviewDecision,
        decide_candidate,
    )
    from sapphire_flow.services.write_principal import resolve_run_principal
    from sapphire_flow.store.audited_writer import make_audited_stores
    from sapphire_flow.store.fine_tuning import PgFineTuningReceipts, lock_decision
    from sapphire_flow.store.tenant_store import PgTenantStore
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.ids import ArtifactId

    path = args.host_config or os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("admin review requires host deployment configuration")
    identity = load_deployment_identity_config(path)
    engine = create_engine_from_env()
    try:
        with engine.begin() as conn:
            principal = resolve_run_principal(
                PgTenantStore(conn),
                identity,
                tenant_code=args.tenant,
                operator=args.operator,
            )
            receipts = PgFineTuningReceipts(conn)
            receipt = receipts.latest(ArtifactId(args.artifact))
            if principal.tenant_id != receipt.tenant_id:
                raise ConfigurationError("candidate is outside the authorized tenant")
            if args.command == "review":
                from sapphire_flow.config.paths import resolve_artifact_dir
                from sapphire_flow.store.model_artifact_store import (
                    PgModelArtifactStore,
                )

                record = PgModelArtifactStore(
                    conn, resolve_artifact_dir()
                ).fetch_artifact_record(receipt.artifact_id)
                sys.stdout.write(
                    json.dumps(
                        {
                            "reviewed_sha256": receipt.sha256,
                            "status": record.status.value
                            if record
                            else "artifact_missing",
                            "receipt": receipt.payload(),
                        },
                        indent=2,
                    )
                    + "\n"
                )
                return 0
            if not identity.promotion_allowed:
                raise ConfigurationError(
                    "host deployment has not enabled admin model decisions"
                )
            lock_decision(conn, receipt)
            # Read again under the lock so an evaluation finishing concurrently
            # cannot be approved using an older report.
            receipt = receipts.latest(ArtifactId(args.artifact))
            prior = receipts.decision(ArtifactId(args.artifact))
            expected = {
                "decision": args.command,
                "reason": args.reason,
                "operator": str(principal.id),
                "tenant_id": str(receipt.tenant_id),
                "reviewed_sha256": args.reviewed_sha256,
            }
            if prior is not None:
                if prior != expected or receipt.sha256 != args.reviewed_sha256:
                    raise ConfigurationError(
                        "candidate already has a different admin decision"
                    )
                log.info(
                    "fine_tuning.decision_already_recorded",
                    artifact_id=str(args.artifact),
                )
                return 0
            stores = make_audited_stores(conn)
            decide_candidate(
                receipt,
                reviewed_sha256=args.reviewed_sha256,
                principal=principal,
                artifacts=cast("ModelArtifactStore", stores["artifact_store"]),
                groups=cast("StationGroupStore", stores["group_store"]),
                stations=cast("StationStore", stores["station_store"]),
                audit=cast("AuditLogStore", stores["audit_log_store"]),
                decision=ReviewDecision(args.command),
                reason=args.reason,
                now=ensure_utc(datetime.now(UTC)),
            )
        log.info(
            "fine_tuning.decision_recorded",
            artifact_id=str(args.artifact),
            decision=args.command,
        )
        return 0
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "start", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True, type=Path)
        if name == "evaluate":
            command.add_argument("--artifact", required=True, type=UUID)
    for name in ("review", "approve", "reject"):
        command = commands.add_parser(name)
        command.add_argument("--artifact", required=True, type=UUID)
        command.add_argument("--tenant", required=True)
        command.add_argument("--operator", required=True)
        command.add_argument("--host-config", default=None)
        if name != "review":
            command.add_argument("--reviewed-sha256", required=True)
            command.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        if args.command in ("review", "approve", "reject"):
            return review_candidate(args)
        request = load_fine_tuning(args.config)
        if args.command == "preview":
            # This is config validation only; the worker checks live authorization,
            # source coverage and donor compatibility before fitting.
            sys.stdout.write(
                json.dumps(
                    {
                        "configuration_sha256": request.sha256,
                        "request": request.payload(),
                        "live_preflight": "not_run",
                    },
                    indent=2,
                )
                + "\n"
            )
            return 0

        async def submit() -> UUID:
            async with get_client() as client:
                return await submit_fine_tuning(
                    request,
                    client,
                    candidate_id=args.artifact if args.command == "evaluate" else None,
                )

        run_id = asyncio.run(submit())
        log.info(
            "fine_tuning.submitted",
            run_id=str(run_id),
            request_id=str(request.request_id),
            configuration_sha256=request.sha256,
        )
        return 0
    except TenantIsolationError:
        log.error(
            "fine_tuning.refused", reason="tenant is outside host write authorization"
        )
        return 1
    except SQLAlchemyError as exc:
        log.error(
            "fine_tuning.database_refused",
            cause=type(exc).__name__,
            reason="operation rolled back; check current state before retrying",
        )
        return 1
    except (OSError, ValueError, ConfigurationError) as exc:
        log.error("fine_tuning.refused", reason=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
