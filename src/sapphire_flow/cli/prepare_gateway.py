"""Facilitator-operated Gateway preparation; no service, database or Prefect startup."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from sapphire_flow.adapters.recap_registration import (
    RecapRegistration,
    validate_connection,
)
from sapphire_flow.config.recap_gateway import (
    build_recap_client_config,
    load_recap_api_key,
    load_recap_gateway_config,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.gateway_preparation import (
    admit_handover,
    apply_subscriptions,
    confirm_proposal,
    confirm_subscriptions,
    history_instructions,
    load_preparation_state,
    preparation_report,
    preparation_workspace,
    preview_subscriptions,
    propose_data,
    reconcile_registration,
    record_history_job,
    register_handover,
    resume_coverage,
    retain_proposal,
    save_preparation_state,
    subscription_revision,
)
from sapphire_flow.services.model_registry import discover_models
from sapphire_flow.types.gateway_preparation import (
    DataProposal,
    DataStream,
    ForecastScope,
    HistoryScope,
    PreparationState,
    ProductContract,
    RegistrationStatus,
    SubscriptionPlan,
)
from sapphire_flow.types.ids import ModelId


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = "cmal_small"
    time_step_seconds: int
    history: HistoryScope | None = None
    forecast: ForecastScope | None = None
    contracts: tuple[ProductContract, ...] = ()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Prepare approved basins in the Gateway. Exit 0: complete source coverage; "
            "2: held/failed requested work. No station import or forecast activation."
        )
    )
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--handover", type=Path, required=True)
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="Inspect/edit a model-based proposal; no Gateway writes"
    )
    prepare.add_argument(
        "--scope",
        type=Path,
        required=True,
        help="Explicit history, forecast and confirmed product contracts JSON",
    )
    prepare.add_argument("--confirm", help="Exact inspected proposal revision")
    prepare.add_argument(
        "--vatro-review",
        help="Existing integration/procedure review evidence reference",
    )
    register = commands.add_parser(
        "register",
        help=(
            "Upload once under existing owner authority; uncertain identity stays held"
        ),
    )
    register.add_argument("--hru", required=True)
    reconcile = commands.add_parser(
        "reconcile",
        help="Record attributed registration evidence; never geometry rechecking",
    )
    reconcile.add_argument(
        "--outcome",
        choices=[
            RegistrationStatus.CONFIRMED.value,
            RegistrationStatus.NOT_CREATED.value,
        ],
        required=True,
    )
    reconcile.add_argument("--actor", required=True)
    reconcile.add_argument("--evidence", required=True)
    subscribe = commands.add_parser(
        "subscribe",
        help="Preview minimal delta; --confirm applies exactly that revision",
    )
    subscribe.add_argument(
        "--confirm", help="Exact subscription delta revision; absent means preview only"
    )
    history = commands.add_parser(
        "history", help="Print manual history instructions; never start jobs"
    )
    history.add_argument(
        "--stream",
        choices=[DataStream.ERA5_HISTORY.value, DataStream.SNOW_HISTORY.value],
    )
    history.add_argument("--actor")
    history.add_argument("--job-reference")
    resume = commands.add_parser(
        "resume", help="Fetch full agreed coverage using the supported client"
    )
    commands.add_parser(
        "status", help="Read aggregate eligible/held outcomes; no Gateway calls"
    )
    for command in (register, subscribe, resume):
        command.add_argument("--config", type=Path, required=True)
        command.add_argument(
            "--secret-file",
            type=Path,
            help="Existing approved secret path; never an API key argument",
        )
    return result


def _show(state: PreparationState, *, instructions: tuple[str, ...] = ()) -> int:
    report = preparation_report(state)
    if state.proposals:
        report["proposal"] = TypeAdapter(DataProposal).dump_python(
            state.proposals[-1], mode="json"
        )
    if state.attempts:
        report["registration_status"] = state.attempts[-1].status.value
        report["registration_hru"] = state.attempts[-1].requested_hru
        report["upload_warnings"] = state.attempts[-1].warnings
    if state.subscription_plans:
        report["subscription_revision"] = subscription_revision(
            state.subscription_plans[-1]
        )
        report["subscription_delta"] = TypeAdapter(SubscriptionPlan).dump_python(
            state.subscription_plans[-1], mode="json"
        )
    if instructions:
        report["manual_history"] = instructions
    sys.stdout.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 2 if any(s["outcome"] == "held" for s in report["stations"]) else 0


def _load_explicit_api_key(path: Path) -> str:
    key: str | None = None
    with suppress(OSError, UnicodeError):
        if path.is_file():
            key = path.read_text().strip()
    if not key:
        raise ConfigurationError("explicit Gateway secret file unavailable or empty")
    return key


def run(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Gateway preparation requires a dedicated CLI process")
    configure_cli_logging()
    # Resolve before any client boundary changes process working directory.
    source = args.handover.absolute()
    workspace = args.workspace.absolute()
    if getattr(args, "config", None):
        args.config = args.config.resolve()
    if getattr(args, "secret_file", None):
        args.secret_file = args.secret_file.resolve()
    state: PreparationState | None = None
    instructions: tuple[str, ...] = ()
    try:
        with preparation_workspace(workspace, handover=source):
            if (workspace / "state.json").exists():
                state = load_preparation_state(workspace)
                if state.handover.directory != source.resolve():
                    raise ValueError("workspace belongs to a different handover")
            elif args.command == "prepare":
                state = PreparationState(handover=admit_handover(source))
                save_preparation_state(workspace, state)
            else:
                raise ValueError("prepare the approved handover first")
            now = datetime.now(UTC).isoformat()
            if args.command == "prepare":
                spec = ProposalInput.model_validate_json(args.scope.read_bytes())
                model = discover_models().get(ModelId(spec.model_id))
                proposal = propose_data(
                    model_id=spec.model_id,
                    requirements=model.data_requirements if model is not None else None,
                    time_step=timedelta(seconds=spec.time_step_seconds),
                    history=spec.history,
                    forecast=spec.forecast,
                    contracts=spec.contracts,
                    configuration_hash=getattr(model, "config_hash", None),
                )
                state = retain_proposal(workspace, state, proposal)
                if args.vatro_review:
                    state = replace(state, gateway_review_reference=args.vatro_review)
                    save_preparation_state(workspace, state)
                if args.confirm:
                    state = confirm_proposal(workspace, state, args.confirm)
            elif args.command == "reconcile":
                state = reconcile_registration(
                    workspace,
                    state,
                    status=RegistrationStatus(args.outcome),
                    actor=args.actor,
                    evidence_reference=args.evidence,
                    now=now,
                )
            elif args.command == "history":
                if args.job_reference:
                    if not args.actor or not args.stream:
                        raise ValueError("job reference needs stream and actor")
                    state = record_history_job(
                        workspace,
                        state,
                        stream=DataStream(args.stream),
                        actor=args.actor,
                        reference=args.job_reference,
                    )
                instructions = history_instructions(state)
            elif args.command in ("register", "subscribe", "resume"):
                config = load_recap_gateway_config(args.config)
                validate_connection(config.base_url, verify_tls=config.verify_tls)
                key = (
                    _load_explicit_api_key(args.secret_file)
                    if args.secret_file is not None
                    else load_recap_api_key()
                )
                if args.command == "resume":
                    from recap_client import RecapClient

                    client: Any = RecapClient(
                        build_recap_client_config(api_key=key, config=config)
                    )
                    state = resume_coverage(workspace, state, client, now=now)
                else:
                    adapter = RecapRegistration(config, api_key=key)
                    try:
                        if args.command == "register":
                            state = register_handover(
                                workspace,
                                state,
                                adapter,
                                requested_hru=args.hru,
                                attempt_id=str(uuid4()),
                                now=now,
                            )
                        elif args.confirm:
                            state = confirm_subscriptions(
                                workspace, state, args.confirm
                            )
                            state = apply_subscriptions(
                                workspace, state, adapter, now=now
                            )
                        else:
                            state = preview_subscriptions(workspace, state, adapter)
                    finally:
                        adapter.close()
            return _show(state, instructions=instructions)
    except (ValueError, OSError, ConfigurationError, ValidationError):
        # Do not print raw third-party exception text, URLs, bodies or chains.
        sys.stderr.write(
            "Gateway preparation held or failed. Inspect local state and "
            "unresolved scope; no automatic retry.\n"
        )
        if state is not None:
            try:
                with preparation_workspace(workspace, handover=source):
                    state = load_preparation_state(workspace)
            except (ValueError, OSError):
                return 2
            _show(state)
        return 2


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
