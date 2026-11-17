#!/usr/bin/env python3
"""Offline desired-configuration preview. Never starts services."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast

import yaml
from cronsim import CronSim, CronSimError

from sapphire_flow.cli.register_deployments import build_deployment_specs
from sapphire_flow.config.deployment_profile import (
    EffectiveSetting,
    EvaluationSummary,
    ProfileDiagnostic,
    ProfileError,
    ProfileEvaluation,
    ProfileSelection,
    ProfileState,
    ScopeKind,
    SecretUse,
    evaluate_deployment_profile,
    select_deployment_profile,
)

SELECTION_GUIDANCE = {
    "PROFILE_AMBIENT_CONFLICT": (
        "A shell configuration selector conflicts with this explicit selection.",
        "Unset the named shell variable for this command or make the "
        "installation agree; do not change live configuration.",
    ),
    "INSTALLATION_SECRET_INVALID": (
        "Bindings include a name not declared as a runtime requirement.",
        "Compare secret_bindings with the selected profile's runtime "
        "secret_requirements; remove undeclared/build bindings.",
    ),
    "PROFILE_SELECTION_CONFLICT": (
        "The installation selects a different manifest.",
        "Set installation agency_profile to the same named profile or canonical "
        "private manifest selected on the command line.",
    ),
    "PROFILE_SCHEMA_INVALID": (
        "The profile does not match schema version 1.",
        "Check required fields, table shapes and unknown keys against the "
        "manifest reference.",
    ),
    "INSTALLATION_SCHEMA_INVALID": (
        "The installation does not match schema version 1.",
        "Check required fields and allowed environment/secret_bindings shapes in "
        "the installation reference.",
    ),
    "PROFILE_PATH_INVALID": (
        "A reference is not a supported literal local path.",
        "Use a file-relative or absolute local path without interpolation, URL "
        "syntax or control characters.",
    ),
    "PROFILE_PATH_DUPLICATE": (
        "An ordered file list repeats a path.",
        "Remove the duplicate while preserving the intended overlay order.",
    ),
    "PROFILE_READ_ERROR": (
        "A selected profile/installation cannot be read as TOML.",
        "Check that the explicitly selected file exists, is readable and has "
        "valid TOML syntax.",
    ),
    "PROFILE_SELECTOR_REQUIRED": (
        "No agency profile was selected.",
        "Specify a shipped name or --profile-file PATH, followed by show or check.",
    ),
    "CLI_INPUT_INVALID": (
        "The command does not match the supported syntax.",
        "Use NAME show|check or --profile-file PATH show|check; see --help.",
    ),
}

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 8 * 1024 * 1024
CONSUMERS = ("init", "api", "prefect-worker", "prefect-worker-ingest")


def diagnostic(code: str, field: str, cause: str, action: str) -> ProfileDiagnostic:
    return ProfileDiagnostic(
        code=code,
        field=field,
        source="desired_compose",
        cause=cause,
        next_action=action,
        owner="developer"
        if code
        in {
            "FORECAST_PROVIDER_PENDING",
            "OPERATOR_METADATA_PENDING",
            "CUSTOM_COMPOSE_PENDING",
            "ENV_FILES_UNCHECKED",
            "RUNBOOK_PENDING",
        }
        else "administrator",
    )


def result(
    evaluation: ProfileEvaluation,
    errors: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
    settings: list[EffectiveSetting],
) -> ProfileEvaluation:
    diagnostics = tuple(dict.fromkeys((*evaluation.diagnostics, *errors)))
    return replace(
        evaluation,
        summary=EvaluationSummary.FAIL
        if diagnostics
        else EvaluationSummary.PENDING
        if pending
        else EvaluationSummary.PASS_OFFLINE,
        diagnostics=diagnostics,
        pending_checks=tuple(pending),
        provenance=(*evaluation.provenance, *settings),
    )


def installed(path: Path, selection: ProfileSelection) -> Path:
    installation = selection.installation
    if installation is None or not path.is_relative_to(ROOT):
        return path
    return installation.project_directory / path.relative_to(ROOT)


def _capture(command: list[str], environment: dict[str, str], directory: Path) -> bytes:
    """Bound both captured streams without writing subprocess output to disk."""
    try:
        with (
            subprocess.Popen(
                command,
                cwd=directory,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as process,
            selectors.DefaultSelector() as selector,
        ):
            if process.stdout is None or process.stderr is None:
                raise ProfileError(
                    "COMPOSE_RENDER_FAILED", "Compose capture is unavailable."
                )
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            chunks: list[bytes] = []
            total = 0
            deadline = time.monotonic() + 20
            while selector.get_map():
                if time.monotonic() >= deadline:
                    process.kill()
                    raise ProfileError(
                        "COMPOSE_RENDER_LIMIT",
                        "Compose exceeded the offline render limit.",
                    )
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > LIMIT:
                        process.kill()
                        raise ProfileError(
                            "COMPOSE_RENDER_LIMIT",
                            "Compose exceeded the offline render limit.",
                        )
                    if key.data:
                        chunks.append(chunk)
            try:
                returncode = process.wait(
                    timeout=max(0.01, deadline - time.monotonic())
                )
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise ProfileError(
                    "COMPOSE_RENDER_LIMIT",
                    "Compose exceeded the offline render limit.",
                ) from None
            if returncode:
                raise ProfileError(
                    "COMPOSE_RENDER_FAILED",
                    "Compose could not render the explicit recipe.",
                )
            return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired):
        raise ProfileError(
            "COMPOSE_RENDER_FAILED",
            "Compose is unavailable or could not render the recipe.",
        ) from None


def render_compose(selection: ProfileSelection) -> dict[str, Any]:
    installation = selection.installation
    if installation is None:
        raise ProfileError(
            "INSTALLATION_REQUIRED", "Supply an explicit installation file."
        )
    # Prevent Compose from loading additional files or fetching remote includes.
    try:
        for path in selection.compose_files:
            if path.stat().st_size > LIMIT:
                raise ProfileError(
                    "COMPOSE_INPUT_UNSUPPORTED",
                    "Compose input exceeds the offline limit.",
                )
            document = yaml.safe_load(path.read_text())
            if not isinstance(document, dict) or "include" in document:
                raise ProfileError(
                    "COMPOSE_INPUT_UNSUPPORTED",
                    "Use an explicit local Compose file list without includes.",
                )
            services = cast("dict[str, Any]", document).get("services", {})
            if not isinstance(services, dict) or any(
                not isinstance(service, dict) or "extends" in service
                for service in cast("dict[str, Any]", services).values()
            ):
                raise ProfileError(
                    "COMPOSE_INPUT_UNSUPPORTED",
                    "Compose extends is not supported by the offline checker.",
                )
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ProfileError(
            "COMPOSE_INPUT_INVALID", "An explicit Compose input cannot be parsed."
        ) from None
    executable = shutil.which("docker-compose")
    command = (
        [executable] if executable else [shutil.which("docker") or "docker", "compose"]
    )
    environment = {
        "PATH": os.defpath,
        "HOME": "/dev/null",
        "DOCKER_CONFIG": "/dev/null",
        **dict(installation.environment),
    }
    try:
        capability = _capture(
            [*command, "config", "--help"], environment, installation.project_directory
        )
    except ProfileError:
        raise ProfileError(
            "COMPOSE_UNAVAILABLE", "Expose a standalone Compose executable on PATH."
        ) from None
    if b"--no-env-resolution" not in capability:
        raise ProfileError(
            "COMPOSE_CAPABILITY_MISSING",
            "Compose must support config --no-env-resolution.",
        )
    command += [
        "--profile",
        "*",
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(installation.project_directory),
        "-p",
        installation.project_name,
    ]
    for path in selection.compose_files:
        command += ["-f", str(path)]
    command += ["config", "--format", "json", "--no-env-resolution"]
    try:
        rendered = json.loads(
            _capture(command, environment, installation.project_directory)
        )
    except (ValueError, UnicodeError):
        raise ProfileError(
            "COMPOSE_OUTPUT_INVALID", "Compose did not return a supported document."
        ) from None
    if not isinstance(rendered, dict) or not isinstance(
        cast("dict[str, Any]", rendered).get("services"), dict
    ):
        raise ProfileError(
            "COMPOSE_OUTPUT_INVALID", "Compose did not return a service mapping."
        )
    return cast("dict[str, Any]", rendered)


def same_path(left: object, right: object) -> bool:
    if not isinstance(left, (str, Path)) or not isinstance(right, (str, Path)):
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _environment(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment", {})
    return (
        {
            key: value
            for key, value in cast("dict[str, Any]", raw).items()
            if isinstance(value, str)
        }
        if isinstance(raw, dict)
        else {}
    )


def _mount(service: dict[str, Any], target: str) -> dict[str, Any] | None:
    matches = [
        cast("dict[str, Any]", item)
        for item in service.get("volumes", [])
        if isinstance(item, dict)
        and cast("dict[str, Any]", item).get("target") == target
    ]
    return matches[0] if len(matches) == 1 else None


def _chain(
    service: dict[str, Any], expected: tuple[Path, ...], selection: ProfileSelection
) -> bool:
    env = _environment(service)
    targets = [
        env.get("SAPPHIRE_CONFIG", ""),
        *filter(None, env.get("SAPPHIRE_CONFIG_OVERLAY", "").split(",")),
    ]
    if len(targets) != len(expected):
        return False
    return all(
        (mount := _mount(service, target)) is not None
        and mount.get("type") == "bind"
        and mount.get("read_only") is True
        and Path(mount.get("source", "")).resolve() == installed(path, selection)
        for target, path in zip(targets, expected, strict=True)
    )


class _ObservedEnvironment(Mapping[str, str]):
    def __init__(self, values: dict[str, str]) -> None:
        self.entries = values
        self.accessed: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.accessed.add(key)
        return self.entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def check_wiring(
    selection: ProfileSelection, evaluation: ProfileEvaluation, rendered: dict[str, Any]
) -> ProfileEvaluation:
    errors: list[ProfileDiagnostic] = []
    pending = [
        item
        for item in evaluation.pending_checks
        if item.code != "COMPOSE_EVIDENCE_PENDING"
    ]
    settings: list[EffectiveSetting] = []
    if selection.profile.runbook is None or not selection.profile.runbook.is_file():
        pending.append(
            diagnostic(
                "RUNBOOK_PENDING",
                "runbook",
                "Matching operational instructions are unavailable.",
                "Supply reviewed instructions for the complete selected recipe "
                "before handoff.",
            )
        )
    services = rendered["services"]
    installation = selection.installation
    if installation is None:
        return evaluation
    expected = (selection.profile.base_config, *selection.profile.overlays)
    for name in CONSUMERS:
        service = services.get(name)
        if not isinstance(service, dict) or not _chain(
            cast("dict[str, Any]", service), expected, selection
        ):
            errors.append(
                diagnostic(
                    "CONFIG_WIRING_INVALID",
                    name,
                    "The mounted configuration chain differs from the evaluated "
                    "profile.",
                    "Restore the complete ordered profile chain and read-only "
                    "file mounts.",
                )
            )
    for index, path in enumerate(expected):
        target = installed(path, selection)
        if target == path:
            continue
        try:
            same = (
                path.stat().st_size <= LIMIT
                and target.stat().st_size <= LIMIT
                and path.read_bytes() == target.read_bytes()
            )
        except OSError:
            same = False
        if not same:
            pending.append(
                diagnostic(
                    "CHECKOUT_CONFIG_UNVERIFIED",
                    f"config[{index}]",
                    "The desired checkout configuration has not been shown "
                    "identical to this preview.",
                    "Evaluate the same checkout and explicit mounted "
                    "configuration files.",
                )
            )
    if any(service.get("env_file") for service in services.values()):
        pending.append(
            diagnostic(
                "ENV_FILES_UNCHECKED",
                "services",
                "Service env_file contents were deliberately not read.",
                "Provide explicit non-secret environment evidence; do not "
                "include credential contents.",
            )
        )
    active_scopes = [
        scope
        for scope in selection.profile.scopes
        if installed(scope.when_compose, selection) in selection.compose_files
    ]
    for scope in active_scopes:
        if scope.kind == ScopeKind.CHWRR_IMPORT:
            operator = services.get("operator", {})
            if scope.base_config is None or not _chain(
                operator, (scope.base_config, *scope.overlays), selection
            ):
                errors.append(
                    diagnostic(
                        "OPERATOR_SCOPE_INVALID",
                        "operator",
                        "The operator must use its fixed independent config chain.",
                        "Use the documented CHWRR operator overlay.",
                    )
                )
            metadata = _mount(operator, "/app/tests/fixtures/dhm/stations.toml")
            if (
                scope.station_metadata is None
                or metadata is None
                or metadata.get("type") != "bind"
                or metadata.get("read_only") is not True
                or not same_path(
                    metadata.get("source"), installed(scope.station_metadata, selection)
                )
            ):
                errors.append(
                    diagnostic(
                        "OPERATOR_METADATA_INVALID",
                        "operator",
                        "The fixed operator metadata mount differs from the "
                        "declared layout.",
                        "Restore the CHWRR station metadata read-only bind.",
                    )
                )
            pending.append(
                diagnostic(
                    "OPERATOR_METADATA_PENDING",
                    "operator",
                    "Operator station metadata contents remain unchecked.",
                    "Check the fixed CHWRR metadata reference using the operator "
                    "runbook.",
                )
            )
        else:
            _check_forecast_scope(services, selection, errors, pending)
    if not selection.profile.inputs and not active_scopes:
        pending = [item for item in pending if item.code != "SCOPED_INPUTS_PENDING"]
    known_files = {
        installed(path, selection) for path in selection.profile.compose_files
    } | {installed(scope.when_compose, selection) for scope in selection.profile.scopes}
    if set(selection.compose_files) - known_files:
        pending.append(
            diagnostic(
                "CUSTOM_COMPOSE_PENDING",
                "compose_files",
                "Additional Compose files require review of their execution semantics.",
                "Review custom commands, images and service applicability before "
                "a handoff.",
            )
        )
    if not any(
        _environment(service).get("SAPPHIRE_BAFU_UNIT_INGEST_CONFIG")
        for service in services.values()
    ):
        pending = [item for item in pending if item.code != "BAFU_REGISTRY_PENDING"]
    _check_secrets(selection, rendered, errors)
    _check_schedules(selection, services, errors, settings)
    return result(evaluation, errors, pending, settings)


def _check_forecast_scope(
    services: dict[str, Any],
    selection: ProfileSelection,
    errors: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
) -> None:
    try:
        from tools.forecast_config import selection_from_worker_chain
    except ModuleNotFoundError:
        from forecast_config import selection_from_worker_chain

    env = dict(selection.installation.environment) if selection.installation else {}
    worker = services.get("prefect-worker", {})
    registration = _environment(services.get("init", {})).get(
        "SAPPHIRE_FORECAST_CONFIG_OVERLAY", ""
    )
    target = "/app/config/overlays/nhs-provider.toml"
    mount = _mount(worker, target)
    try:
        wanted = selection_from_worker_chain(
            _environment(worker).get("SAPPHIRE_CONFIG_OVERLAY", "")
        ).environment_value()
        valid = not registration or registration == wanted
    except ValueError:
        valid = False
    valid = (
        valid
        and mount is not None
        and mount.get("type") == "bind"
        and mount.get("read_only") is True
        and mount.get("bind", {}).get("create_host_path") is False
        and same_path(mount.get("source"), env.get("NHS_PROVIDER_CONFIG_SOURCE"))
    )
    valid = valid and not any(
        _environment(service).get("SAPPHIRE_FORECAST_CONFIG_OVERLAY")
        for name, service in services.items()
        if name != "init"
    )
    if not valid:
        errors.append(
            diagnostic(
                "FORECAST_SCOPE_INVALID",
                "forecast_only",
                "Forecast-only registration or provider mounting violates its scope.",
                "Restore the STANDARD worker prefix and provider-only suffix "
                "using the NHS runbook.",
            )
        )
    pending.append(
        diagnostic(
            "FORECAST_PROVIDER_PENDING",
            "forecast_only",
            "Provider input semantics and ingest-provider parity remain unchecked.",
            "Complete the NHS provider-file and ingest comparison checks before "
            "activation.",
        )
    )


def _check_secrets(
    selection: ProfileSelection,
    rendered: dict[str, Any],
    errors: list[ProfileDiagnostic],
) -> None:
    services = rendered["services"]
    definitions = rendered.get("secrets", {})
    bindings = (
        dict(selection.installation.secret_bindings) if selection.installation else {}
    )
    requirements = [
        item
        for item in selection.profile.secret_requirements
        if item.when_compose is None
        or installed(item.when_compose, selection) in selection.compose_files
    ]
    for use in SecretUse:
        actual: dict[str, set[str]] = {}
        for name, service in services.items():
            owner = service if use == SecretUse.RUNTIME else service.get("build", {})
            grants: list[Any] = (
                cast("dict[str, Any]", owner).get("secrets", [])
                if isinstance(owner, dict)
                else []
            )
            for grant in grants:
                source = (
                    cast("dict[str, Any]", grant).get("source")
                    if isinstance(grant, dict)
                    else grant
                )
                if isinstance(source, str):
                    actual.setdefault(source, set()).add(name)
        wanted = {
            item.name: set(item.consumers) for item in requirements if item.use == use
        }
        if actual != wanted:
            errors.append(
                diagnostic(
                    "SECRET_GRANTS_INVALID",
                    f"secrets.{use.value}",
                    "Secret consumers differ from the declared logical requirements.",
                    "Restore least-privilege grants, including bootstrap consumers.",
                )
            )
    for service in services.values():
        grants = service.get("secrets", [])
        targets = {
            str(cast("dict[str, Any]", grant).get("source")): str(
                cast("dict[str, Any]", grant).get("target")
                or cast("dict[str, Any]", grant).get("source")
            ).removeprefix("/run/secrets/")
            for grant in grants
            if isinstance(grant, dict)
        }
        if any(source != target for source, target in targets.items()):
            errors.append(
                diagnostic(
                    "SECRET_TARGET_INVALID",
                    "secrets",
                    "A runtime secret target alias changes its expected delivery name.",
                    "Restore the logical runtime secret target names.",
                )
            )
        for key, value in _environment(service).items():
            if (
                key == "DB_PASSWORD_SECRET"
                or key.endswith("_PASSWORD_FILE")
                or key == "ACCESS_TOKEN_PEPPER_FILE"
            ) and (
                value.removeprefix("/run/secrets/") not in targets.values()
                or not value.startswith("/run/secrets/")
            ):
                errors.append(
                    diagnostic(
                        "SECRET_INPUT_INVALID",
                        "secrets",
                        "A credential file input does not reference a granted secret.",
                        "Align credential file inputs and least-privilege grants.",
                    )
                )
    for item in requirements:
        definition = definitions.get(item.name, {})
        if item.use == SecretUse.BUILD:
            valid = (
                definition.get("environment") == item.environment_name
                and "file" not in definition
            )
        elif item.name in bindings:
            valid = (
                same_path(definition.get("file"), bindings[item.name])
                and "environment" not in definition
            )
        else:
            continue
        if not valid:
            errors.append(
                diagnostic(
                    "SECRET_BINDING_INVALID",
                    "secret_bindings",
                    "A secret delivery declaration disagrees with the selected "
                    "installation.",
                    "Align logical delivery paths or build environment names; do "
                    "not supply values.",
                )
            )


def _check_schedules(
    selection: ProfileSelection,
    services: dict[str, Any],
    errors: list[ProfileDiagnostic],
    settings: list[EffectiveSetting],
) -> None:
    env = _ObservedEnvironment(_environment(services.get("init", {})))
    try:
        specs = build_deployment_specs(env)
        for spec in specs:
            if spec.cron is not None:
                CronSim(spec.cron, datetime(2026, 1, 1, tzinfo=UTC))
    except (ValueError, TypeError, CronSimError):
        errors.append(
            diagnostic(
                "REGISTRAR_INPUT_INVALID",
                "schedules",
                "Registrar schedule or selection inputs are invalid.",
                "Use supported deployment names and valid cron expressions.",
            )
        )
        return
    supplied = (
        dict(selection.installation.environment) if selection.installation else {}
    )
    for key, value in supplied.items():
        if (key.startswith("SCHEDULE_") or key == "SAPPHIRE_SKIP_DEPLOYMENTS") and (
            key not in env.accessed or env.entries.get(key) != value
        ):
            errors.append(
                diagnostic(
                    "SCHEDULE_FORWARDING_INVALID",
                    "schedules",
                    "A requested registrar setting is unsupported or not "
                    "forwarded to init.",
                    "Use a supported setting and forward it through the complete "
                    "Compose recipe.",
                )
            )
    defaults = {spec.deployment_name: spec for spec in build_deployment_specs({})}
    if "SCHEDULE_COLLECT_BAFU_FORECASTS" not in env.entries:
        settings.append(
            EffectiveSetting(
                field="jobs.collect-bafu-forecasts.forwarding",
                source="registrar_spec_and_init_environment",
                operation="default",
                value="not_forwarded_using_registrar_default",
                visibility="value",
            )
        )
    pools = {
        "default": "prefect-worker",
        "ingest": "prefect-worker-ingest",
        "backup": "prefect-worker-backup",
    }
    for pool in {spec.work_pool_name for spec in specs}:
        command = services.get(pools.get(pool, ""), {}).get("command", [])
        tokens = command.split() if isinstance(command, str) else command
        if not any(
            tokens[index : index + 2] == ["--pool", pool]
            for index in range(len(tokens))
        ):
            errors.append(
                diagnostic(
                    "WORKER_POOL_INVALID",
                    "workers",
                    "A registrar pool has no matching desired worker command.",
                    "Restore the matching default, ingest and backup worker commands.",
                )
            )
    active = {spec.deployment_name for spec in specs}
    for name in defaults:
        settings.append(
            EffectiveSetting(
                field=f"jobs.{name}.selection",
                source="registrar_spec_and_init_environment",
                operation="set",
                value="enabled" if name in active else "skipped",
                visibility="value",
            )
        )
    for spec in specs:
        overridden = spec.cron != defaults[spec.deployment_name].cron
        # Names/pools originate in the fixed registrar, cron is deliberately redacted.
        settings.append(
            EffectiveSetting(
                field=f"jobs.{spec.deployment_name}.schedule",
                source="registrar_spec_and_init_environment",
                operation="set",
                value=None if overridden else defaults[spec.deployment_name].cron,
                visibility="redacted" if overridden else "value",
            )
        )
        settings.append(
            EffectiveSetting(
                field=f"jobs.{spec.deployment_name}.pool",
                source="registrar_spec",
                operation="default",
                value=spec.work_pool_name,
                visibility="value",
            )
        )


def check_profile(
    selection: ProfileSelection, evaluation: ProfileEvaluation
) -> ProfileEvaluation:
    if selection.installation is None or evaluation.diagnostics:
        return evaluation
    try:
        from tools.compose_release_identity import (
            ComposeReleaseIdentityError,
            validate_env,
        )
    except ModuleNotFoundError:
        from compose_release_identity import ComposeReleaseIdentityError, validate_env
    environment = dict(selection.installation.environment)
    keys = {"VERSION", "SAPPHIRE_RELEASE_VERSION", "SAPPHIRE_SOURCE_REVISION"}
    if not keys <= environment.keys():
        return result(
            evaluation,
            [],
            [
                *evaluation.pending_checks,
                diagnostic(
                    "RELEASE_IDENTITY_PENDING",
                    "release",
                    "Release identity inputs are incomplete.",
                    "Supply the intended version and full source revision.",
                ),
            ],
            [],
        )
    try:
        validate_env(environment)
    except ComposeReleaseIdentityError:
        return result(
            evaluation,
            [
                diagnostic(
                    "RELEASE_IDENTITY_INVALID",
                    "release",
                    "Release identity inputs disagree.",
                    "Use consistent version and source revision inputs.",
                )
            ],
            list(evaluation.pending_checks),
            [],
        )
    try:
        rendered = render_compose(selection)
        return check_wiring(selection, evaluation, rendered)
    except ProfileError as exc:
        if exc.code == "COMPOSE_INPUT_INVALID":
            return result(
                evaluation,
                [
                    diagnostic(
                        exc.code,
                        "compose",
                        "The explicit Compose input is invalid.",
                        "Correct the local Compose document before checking again.",
                    )
                ],
                list(evaluation.pending_checks),
                [],
            )
        pending = [
            *evaluation.pending_checks,
            diagnostic(
                exc.code,
                "compose",
                "Compose is unavailable under isolated configuration."
                if exc.code == "COMPOSE_UNAVAILABLE"
                else "Compose lacks config --no-env-resolution support."
                if exc.code == "COMPOSE_CAPABILITY_MISSING"
                else "The isolated Compose render could not establish evidence.",
                "Install a trusted standalone docker-compose on PATH, or a "
                "system-discoverable docker compose plugin; config "
                "--no-env-resolution is required. Check explicit recipe inputs "
                "if rendering still fails; captured output is never printed.",
            ),
        ]
        return result(evaluation, [], pending, [])


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProfileError(
            "CLI_INPUT_INVALID",
            "Use a named profile or --profile-file, followed by show or check.",
        )


def exit_code(command: str, evaluation: ProfileEvaluation, state: ProfileState) -> int:
    if command == "show":
        invalid = evaluation.diagnostics
        if state == ProfileState.INCOMPLETE:
            invalid = tuple(item for item in invalid if item.code != "INPUT_REQUIRED")
        return 1 if invalid else 0
    return {
        EvaluationSummary.FAIL: 1,
        EvaluationSummary.PENDING: 2,
        EvaluationSummary.PASS_OFFLINE: 0,
    }[evaluation.summary]


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_output = (
        any(
            args[index : index + 2] == ["--format", "json"]
            for index in range(len(args))
        )
        or "--format=json" in args
    )
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "selection",
        nargs="*",
        help="NAME show|check, or show|check with --profile-file",
    )
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    selection: ProfileSelection | None = None
    command = "show"
    try:
        options = parser.parse_args(args)
        tokens = options.selection
        if options.profile_file is not None and len(tokens) == 1:
            name, command = None, tokens[0]
        elif options.profile_file is None and len(tokens) == 2:
            name, command = tokens
        else:
            raise ProfileError("CLI_INPUT_INVALID", "Select one profile and command.")
        if command not in {"show", "check"}:
            raise ProfileError("CLI_INPUT_INVALID", "Choose show or check.")
        selection = select_deployment_profile(
            name=name,
            profile_file=options.profile_file,
            installation_file=options.installation,
            ambient_environment=os.environ,
        )
        environment = (
            dict(selection.installation.environment) if selection.installation else {}
        )
        evaluation = evaluate_deployment_profile(selection, environment=environment)
        if command == "check":
            evaluation = check_profile(selection, evaluation)
        code = exit_code(command, evaluation, selection.profile.state)
    except ProfileError as exc:
        evaluation = ProfileEvaluation(
            summary=EvaluationSummary.FAIL,
            diagnostics=(
                diagnostic(
                    exc.code,
                    exc.field,
                    *SELECTION_GUIDANCE.get(
                        exc.code,
                        (
                            "A profile declaration is invalid.",
                            "Check the named diagnostic code and manifest "
                            "constraints in the profile reference; ask "
                            "deployment support if unresolved.",
                        ),
                    ),
                ),
            ),
            provenance=(),
            pending_checks=(),
            unverified_runtime=(
                "actual_host_configuration",
                "credential_availability",
                "registered_jobs_and_pools",
                "provider_access",
                "build_credentials",
            ),
        )
        code = 1
    support: dict[str, Any] = {"selection": None, "desired_source_revision": None}
    if selection is not None:
        support["selection"] = {
            "profile": selection.profile.name
            if selection.profile.manifest_path
            in {
                ROOT / "deploy/profiles/agency" / f"{name}.toml"
                for name in ("swiss", "nepal", "kazhydromet")
            }
            else "private-profile",
            "compose_files": [
                f"compose[{index}]" for index, _ in enumerate(selection.compose_files)
            ],
            "config_files": [
                "base",
                *[
                    f"overlay[{index}]"
                    for index, _ in enumerate(selection.profile.overlays)
                ],
            ],
            "installation": "supplied" if selection.installation else "missing",
        }
        source = (
            dict(selection.installation.environment).get("SAPPHIRE_SOURCE_REVISION", "")
            if selection.installation
            else ""
        )
        if len(source) == 40 and all(
            character in "0123456789abcdef" for character in source
        ):
            support["desired_source_revision"] = source
    report = {
        "schema_version": 1,
        "evidence_scope": "desired_configuration_unverified_against_host",
        "launching": "not_available",
        "support": support,
        **asdict(evaluation),
    }
    if json_output:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        sys.stdout.write(
            f"{evaluation.summary}: desired configuration, unverified against host.\n"
            "Profile-driven launching is not available.\n"
        )
        if selection is not None:
            if selection.installation is not None:
                sys.stdout.write(
                    "Desired project directory: "
                    f"{str(selection.installation.project_directory)!r}\n"
                )
            sys.stdout.write("Ordered Compose selection (local desired paths):\n")
            for index, path in enumerate(selection.compose_files):
                sys.stdout.write(f"  compose[{index}]: {str(path)!r}\n")
            if (
                evaluation.summary == EvaluationSummary.PASS_OFFLINE
                and command == "check"
            ):
                sys.stdout.write(
                    f"Offline handoff: {str(selection.profile.runbook)!r}\n"
                )
                if any(
                    scope.kind == ScopeKind.FORECAST_ONLY
                    and installed(scope.when_compose, selection)
                    in selection.compose_files
                    for scope in selection.profile.scopes
                ):
                    sys.stdout.write(
                        "Also follow docs/deployment/nhs-forecast-configuration.md.\n"
                    )
                sys.stdout.write(
                    "Keep the complete ordered file list; a shorter host-runbook "
                    "example does not replace it.\n"
                )
        for item in (*evaluation.diagnostics, *evaluation.pending_checks):
            sys.stdout.write(
                f"{item.code} [{item.field}; owner={item.owner}]: "
                f"{item.cause} {item.next_action}\n"
            )
        for item in evaluation.provenance:
            value = item.value if item.visibility == "value" else f"<{item.visibility}>"
            sys.stdout.write(
                f"{item.field} = {value} ({item.source}; {item.operation})\n"
            )
        sys.stdout.write(
            "Unverified runtime: " + ", ".join(evaluation.unverified_runtime) + "\n"
        )
        sys.stdout.write("Reference: docs/deployment/profiles.md\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
