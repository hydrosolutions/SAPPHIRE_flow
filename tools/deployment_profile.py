#!/usr/bin/env python3
"""Offline config and service-wiring consistency checks. Never starts services."""

from __future__ import annotations

import argparse
import json
import math
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
from typing import Any, NamedTuple, NoReturn, cast

import yaml
from cronsim import CronSim, CronSimError

from sapphire_flow.cli.register_deployments import build_deployment_specs
from sapphire_flow.config.cors import parse_exact_origin
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
from sapphire_flow.exceptions import ConfigurationError

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
        "Set installation agency_profile to the same canonical private manifest "
        "selected on the command line.",
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
        "Specify --profile-file PATH, followed by show or check.",
    ),
    "CLI_INPUT_INVALID": (
        "The command does not match the supported syntax.",
        "Use --profile-file PATH show|check; see --help.",
    ),
}

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 8 * 1024 * 1024
CONSUMERS = (
    "init",
    "api",
    "prefect-worker",
    "prefect-worker-ingest",
)
_CONFIG_CONSUMERS = (*CONSUMERS, "prefect-worker-backup")
_STANDARD_SERVICES = frozenset(
    (*_CONFIG_CONSUMERS, "postgres", "prefect-server", "caddy")
)


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
            "EXECUTION_EXTENSION_PENDING",
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
            if any(
                "env_file" in service
                for service in cast("dict[str, Any]", services).values()
            ):
                # Older Compose reads env_file even with --no-env-resolution.
                raise ProfileError(
                    "ENV_FILES_UNCHECKED",
                    "Service env_file evidence is deferred without reading files "
                    "or invoking Compose.",
                )
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ProfileError(
            "COMPOSE_INPUT_INVALID", "An explicit Compose input cannot be parsed."
        ) from None
    environment = {
        "PATH": os.defpath,
        "HOME": "/dev/null",
        "DOCKER_CONFIG": "/dev/null",
        **dict(installation.environment),
    }
    # Docker CLI can stop discovery on ENOTDIR beneath /dev/null. Invoke only
    # Compose itself: PATH or Docker's fixed system plugin locations, never ~/.docker.
    candidates = [shutil.which("docker-compose")]
    candidates.extend(
        shutil.which("docker-compose", path=directory)
        for directory in (
            "/usr/local/lib/docker/cli-plugins",
            "/usr/local/libexec/docker/cli-plugins",
            "/usr/lib/docker/cli-plugins",
            "/usr/libexec/docker/cli-plugins",
        )
    )
    command: list[str] = []
    failure_code = "COMPOSE_UNAVAILABLE"
    for executable in dict.fromkeys(path for path in candidates if path is not None):
        try:
            capability = _capture(
                [executable, "config", "--help"],
                environment,
                installation.project_directory,
            )
        except ProfileError:
            continue
        if b"--no-env-resolution" in capability:
            command = [executable]
            break
        failure_code = "COMPOSE_CAPABILITY_MISSING"
    if not command:
        raise ProfileError(
            failure_code,
            "A trusted Compose executable supporting config --no-env-resolution "
            "must be installed on PATH or in a standard system plugin directory.",
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
    captured = _capture(command, environment, installation.project_directory)
    try:
        rendered = json.loads(captured)
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
    selection: ProfileSelection,
    evaluation: ProfileEvaluation,
    rendered: dict[str, Any],
    reference: dict[str, Any] | None = None,
) -> ProfileEvaluation:
    errors: list[ProfileDiagnostic] = []
    pending = [
        item
        for item in evaluation.pending_checks
        if item.code != "COMPOSE_EVIDENCE_PENDING"
    ]
    settings: list[EffectiveSetting] = []
    bindings = (
        dict(selection.installation.secret_bindings) if selection.installation else {}
    )
    if not _readable_runbook(selection.profile.runbook, tuple(bindings.values())):
        pending.append(
            diagnostic(
                "RUNBOOK_PENDING",
                "runbook",
                "A readable nonempty local handoff reference is missing.",
                "Supply a readable local runbook; its instructions are not approved.",
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
    if _check_execution(selection, rendered, reference, errors, pending):
        pending = [
            item for item in pending if item.code != "EXECUTION_COVERAGE_PENDING"
        ]
        pending = [item for item in pending if item.code != "CUSTOM_COMPOSE_PENDING"]
    elif not any(item.code == "EXECUTION_COVERAGE_PENDING" for item in pending):
        pending.append(
            diagnostic(
                "EXECUTION_COVERAGE_PENDING",
                "execution",
                "Complete standard-topology execution has not been established.",
                "Use the supported service recipe and forecast/backup job subset.",
            )
        )
    return result(evaluation, errors, pending, settings)


def _readable_runbook(path: Path | None, secrets: tuple[Path, ...]) -> bool:
    if path is None or any(same_path(path, secret) for secret in secrets):
        return False
    try:
        names = (path.name, path.resolve().name)
        if any(name == ".env" or name.startswith(".env.") for name in names):
            return False
        if not 0 < path.stat().st_size <= 256 * 1024 or not path.is_file():
            return False
        with path.open("rb") as stream:
            content = stream.read(256 * 1024 + 1)
        return len(content) <= 256 * 1024 and bool(content.decode("utf-8").strip())
    except (OSError, UnicodeError, RuntimeError):
        return False


def _execution_issue(pending: list[ProfileDiagnostic], field: str, cause: str) -> None:
    pending.append(
        diagnostic(
            "EXECUTION_EXTENSION_PENDING",
            field,
            cause,
            "Use the checked recipe or obtain coverage for this extension.",
        )
    )


def _check_execution(
    selection: ProfileSelection,
    rendered: dict[str, Any],
    reference: dict[str, Any] | None,
    errors: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
) -> bool:
    installation = selection.installation
    if reference is None or installation is None:
        return False
    if not same_path(installation.project_directory, ROOT):
        _execution_issue(
            pending,
            "project_directory",
            "Run the checker from the same source checkout as the installation.",
        )
        return False
    initial = (len(errors), len(pending))
    services = rendered["services"]
    supplied = dict(installation.environment)
    if any(
        supplied.get(name)
        for name in (
            "NHS_FORECAST_JOB_OVERLAYS",
            "NHS_PROVIDER_CONFIG_SOURCE",
            "SAPPHIRE_DHM_DELIVERY_DIR",
        )
    ):
        _execution_issue(
            pending,
            "environment",
            "An optional provider or delivery declaration requires separate coverage.",
        )
    _check_selected_jobs(services, supplied, errors, pending)
    if len(pending) != initial[1] or errors:
        return False
    canonical = reference.get("services", {})
    if set(canonical) != set(_STANDARD_SERVICES) or set(services) != set(
        _STANDARD_SERVICES
    ):
        _execution_issue(
            pending,
            "services",
            "Additional or missing services prevent standard-topology coverage.",
        )
        return False
    if any(
        installed(scope.when_compose, selection) in selection.compose_files
        for scope in selection.profile.scopes
    ):
        _execution_issue(
            pending,
            "scopes",
            "Exceptional execution scopes remain outside standard-topology coverage.",
        )
    for key in ("networks", "volumes"):
        if rendered.get(key) != reference.get(key):
            _execution_issue(
                pending,
                key,
                "Network or volume definitions differ from the checked recipe.",
            )
    if any(
        rendered.get(key) != reference.get(key)
        for key in (set(rendered) | set(reference))
        - {"services", "networks", "volumes", "secrets"}
    ):
        _execution_issue(
            pending,
            "compose",
            "Additional Compose execution declarations remain unchecked.",
        )
    if set(rendered.get("secrets", {})) != set(reference.get("secrets", {})):
        _execution_issue(
            pending,
            "secrets",
            "Secret declarations differ from the standard credential roles.",
        )
    supplied = dict(installation.environment)
    for name in sorted(_STANDARD_SERVICES):
        service, expected = services[name], canonical[name]
        for key in set(service) | set(expected):
            if key in {"environment", "volumes"}:
                continue
            if service.get(key) == expected.get(key):
                continue
            if key in {"image", "command", "entrypoint"}:
                errors.append(
                    diagnostic(
                        "EXECUTION_WIRING_INVALID",
                        name,
                        "Image, command or entrypoint differs from the checked recipe.",
                        "Restore the intended image and reviewed execution.",
                    )
                )
            else:
                _execution_issue(
                    pending,
                    name,
                    "A service execution setting differs from the checked recipe.",
                )
        _check_execution_environment(name, service, expected, supplied, errors, pending)
        if not _standard_mounts(name, service, expected):
            _execution_issue(
                pending, name, "Mounts differ from the bounded config/data layout."
            )
    if not _shared_storage_consistent(services, canonical):
        errors.append(
            diagnostic(
                "SHARED_STORAGE_INVALID",
                "volumes",
                "Consumers of a shared data volume resolve to different storage.",
                "Use one source and mount type per shared volume.",
            )
        )
    expected_chain = (selection.profile.base_config, *selection.profile.overlays)
    if not _chain(services["prefect-worker-backup"], expected_chain, selection):
        errors.append(
            diagnostic(
                "CONFIG_WIRING_INVALID",
                "prefect-worker-backup",
                "The backup mount does not preserve the selected config chain.",
                "Restore the mount; backup does not parse application TOML.",
            )
        )
    _check_api_inputs(services, supplied, errors, pending)
    return not errors and initial[1] == len(pending)


def _check_execution_environment(
    name: str,
    service: dict[str, Any],
    expected: dict[str, Any],
    supplied: dict[str, str],
    errors: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
) -> None:
    actual, baseline = _environment(service), _environment(expected)
    if len(actual) != len(service.get("environment", {})):
        _execution_issue(
            pending,
            name,
            "Environment inputs include unresolved or unsupported values.",
        )
    allowed: dict[str, str] = {}
    if name in _CONFIG_CONSUMERS:
        allowed.update(
            {
                key: actual[key]
                for key in ("SAPPHIRE_CONFIG", "SAPPHIRE_CONFIG_OVERLAY")
                if key in actual
            }
        )
    if name == "init":
        allowed.update(
            {
                key: value
                for key, value in supplied.items()
                if key.startswith("SCHEDULE_")
                or key in {"SAPPHIRE_ONLY_DEPLOYMENTS", "SAPPHIRE_SKIP_DEPLOYMENTS"}
            }
        )
        if actual.get("SAPPHIRE_FLOW_SOURCE_ROOT") == "/app":
            allowed["SAPPHIRE_FLOW_SOURCE_ROOT"] = "/app"
    for consumer, key in (
        ("prefect-worker", "SAPPHIRE_REQUIRE_NWP"),
        ("prefect-worker-backup", "SAPPHIRE_BACKUP_DUMP_TIMEOUT_S"),
        ("caddy", "SAPPHIRE_DOMAIN"),
    ):
        if name == consumer and key in supplied:
            allowed[key] = supplied[key]
            if actual.get(key) != supplied[key]:
                errors.append(
                    diagnostic(
                        "ENVIRONMENT_FORWARDING_INVALID",
                        name,
                        "A declared service input is not forwarded unchanged.",
                        "Forward the declared nonsecret setting into its consumer.",
                    )
                )
    for key in set(actual) | set(baseline):
        if actual.get(key) == allowed.get(key, baseline.get(key)):
            continue
        if key in baseline:
            errors.append(
                diagnostic(
                    "SERVICE_ENVIRONMENT_INVALID",
                    name,
                    "A service setting differs from its role or declared inputs.",
                    "Restore standard role, database, Prefect and service settings.",
                )
            )
        else:
            _execution_issue(
                pending,
                name,
                "Additional environment-selected behavior lacks coverage.",
            )
    if name == "prefect-worker-backup" and "SAPPHIRE_BACKUP_DUMP_TIMEOUT_S" in actual:
        try:
            timeout = float(actual["SAPPHIRE_BACKUP_DUMP_TIMEOUT_S"])
            valid = math.isfinite(timeout) and timeout > 0
        except ValueError:
            valid = False
        if not valid:
            errors.append(
                diagnostic(
                    "BACKUP_TIMEOUT_INVALID",
                    name,
                    "The explicit backup dump timeout is not a positive finite number.",
                    "Supply a positive timeout in seconds or omit the override.",
                )
            )


def _standard_mounts(
    name: str, service: dict[str, Any], reference: dict[str, Any]
) -> bool:
    env = _environment(service)
    targets = (
        [
            env.get("SAPPHIRE_CONFIG", ""),
            *filter(None, env.get("SAPPHIRE_CONFIG_OVERLAY", "").split(",")),
        ]
        if name in _CONFIG_CONSUMERS
        else []
    )
    if targets and (
        targets[0] != "/app/config.toml"
        or any(
            Path(target).parent != Path("/app/config/overlays")
            or Path(target).suffix != ".toml"
            for target in targets[1:]
        )
    ):
        return False
    mounts = service.get("volumes", [])
    baseline = {mount["target"]: mount for mount in reference.get("volumes", [])}
    if len({item.get("target") for item in mounts}) != len(mounts):
        return False
    remaining = dict(baseline)
    for mount in mounts:
        target = mount.get("target")
        if target in targets:
            if set(mount) - {"type", "source", "target", "read_only", "bind"} or set(
                mount.get("bind", {})
            ) - {"create_host_path"}:
                return False
            remaining.pop("/app/config.toml", None)
            continue  # Complete file identity/order/read-only checked by _chain.
        expected = remaining.pop(target, None)
        if expected is None:
            return False
        if mount == expected:
            continue
        # Permit host data storage replacing standard named volumes, preserving
        # target, access mode and no propagation/driver-specific behavior.
        if expected.get("type") != "volume" or not target.startswith("/data/"):
            return False
        if (
            mount.get("type") != "bind"
            or not Path(mount.get("source", "")).is_absolute()
        ):
            return False
        if mount.get("read_only", False) != expected.get("read_only", False):
            return False
        if set(mount) - {"type", "source", "target", "read_only", "bind"} or set(
            mount.get("bind", {})
        ) - {"create_host_path"}:
            return False
    return not remaining


def _shared_storage_consistent(
    services: dict[str, Any], canonical: dict[str, Any]
) -> bool:
    sources: dict[str, set[tuple[str, str]]] = {}
    for name, service in canonical.items():
        for expected in service.get("volumes", []):
            if expected.get("type") != "volume":
                continue
            actual = _mount(services[name], expected["target"])
            if actual is None:
                return False
            kind = actual.get("type", "")
            source = actual.get("source", "")
            if kind == "bind":
                source = str(Path(source).resolve())
            sources.setdefault(expected["source"], set()).add((kind, source))
    return all(len(bindings) == 1 for bindings in sources.values())


def _check_selected_jobs(
    services: dict[str, Any],
    supplied: dict[str, str],
    errors: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
) -> None:
    environment = _environment(services.get("init", {}))
    selected = {"forecast-cycle", "backup-database"}
    try:
        actual = build_deployment_specs(environment)
        expected = build_deployment_specs(
            {"SAPPHIRE_ONLY_DEPLOYMENTS": ",".join(sorted(selected))}
        )
    except ValueError:
        return  # _check_schedules supplies the invalid-input diagnostic.
    if (
        not supplied.get("SAPPHIRE_ONLY_DEPLOYMENTS")
        or {item.deployment_name for item in actual} != selected
    ):
        _execution_issue(
            pending,
            "jobs",
            "Only explicit forecast-cycle and backup-database jobs have coverage.",
        )
        return
    if (
        environment.get("SAPPHIRE_ONLY_DEPLOYMENTS")
        != supplied["SAPPHIRE_ONLY_DEPLOYMENTS"]
    ):
        errors.append(
            diagnostic(
                "SCHEDULE_FORWARDING_INVALID",
                "jobs",
                "The desired subset is not forwarded to init.",
                "Forward SAPPHIRE_ONLY_DEPLOYMENTS unchanged into init.",
            )
        )
    baseline = {item.deployment_name: item for item in expected}
    for spec in actual:
        standard = baseline[spec.deployment_name]
        if (
            spec.flow_module,
            spec.flow_attr,
            spec.work_pool_name,
            spec.concurrency_limit,
            spec.forecast_config_overlay,
        ) != (
            standard.flow_module,
            standard.flow_attr,
            standard.work_pool_name,
            standard.concurrency_limit,
            None,
        ):
            _execution_issue(
                pending,
                "jobs",
                "A selected job differs from its fresh-registration specification.",
            )
    if environment.get("SAPPHIRE_FLOW_SOURCE_ROOT", "/app") != "/app" or any(
        _environment(service).get(key)
        for service in services.values()
        for key in ("SAPPHIRE_FORECAST_CONFIG_OVERLAY", "NHS_FORECAST_JOB_OVERLAYS")
    ):
        _execution_issue(
            pending,
            "jobs",
            "Job source-root or config overrides require separate coverage.",
        )


def _check_api_inputs(
    services: dict[str, Any],
    supplied: dict[str, str],
    errors: list[ProfileDiagnostic],
    pending: list[ProfileDiagnostic],
) -> None:
    api = _environment(services["api"])
    if any(value for key, value in api.items() if key.startswith("SAPPHIRE_HUMAN_")):
        _execution_issue(
            pending,
            "api",
            "Named-human authentication is outside the supported offline path.",
        )
    try:
        if (
            "SAPPHIRE_CORS_ORIGINS" not in supplied
            or api.get("SAPPHIRE_CORS_ORIGINS") != supplied["SAPPHIRE_CORS_ORIGINS"]
        ):
            raise ValueError("Explicit origin declaration required")
        for value in supplied["SAPPHIRE_CORS_ORIGINS"].split(","):
            if value.strip():
                parse_exact_origin(value.strip())
        domain = supplied.get("SAPPHIRE_DOMAIN", "")
        if domain != ":80":
            parse_exact_origin("https://" + domain)
            if not domain or any(
                not char.isascii() or not (char.isalnum() or char in ".-")
                for char in domain
            ):
                raise ValueError("Named public route required")
    except (ConfigurationError, ValueError):
        errors.append(
            diagnostic(
                "API_ROUTE_INVALID",
                "api",
                "Explicit allowed origins or the public route are missing or invalid.",
                "Declare exact origins (empty means same-origin), and a named domain "
                "or explicit :80 HTTP test route.",
            )
        )


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
        and mount.get("bind", {}).get("create_host_path", False) is False
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
        if (
            key.startswith("SCHEDULE_")
            or key in {"SAPPHIRE_SKIP_DEPLOYMENTS", "SAPPHIRE_ONLY_DEPLOYMENTS"}
        ) and (key not in env.accessed or env.entries.get(key) != value):
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
                default_status="review",
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
                operation="set" if overridden else "default",
                value=None if overridden else defaults[spec.deployment_name].cron,
                visibility="redacted" if overridden else "value",
                default_status="explicit" if overridden else "review",
            )
        )
        settings.append(
            EffectiveSetting(
                field=f"jobs.{spec.deployment_name}.pool",
                source="registrar_spec",
                operation="default",
                value=spec.work_pool_name,
                visibility="value",
                default_status="review",
            )
        )


class CheckedProfile(NamedTuple):
    evaluation: ProfileEvaluation
    recipe: dict[str, Any] | None


def inspect_profile(
    selection: ProfileSelection, evaluation: ProfileEvaluation
) -> CheckedProfile:
    captured: list[dict[str, Any]] = []
    checked = _check_profile(selection, evaluation, captured)
    return CheckedProfile(checked, captured[0] if captured else None)


def check_profile(
    selection: ProfileSelection, evaluation: ProfileEvaluation
) -> ProfileEvaluation:
    return inspect_profile(selection, evaluation).evaluation


def _check_profile(
    selection: ProfileSelection,
    evaluation: ProfileEvaluation,
    captured: list[dict[str, Any]],
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
        reference = render_compose(
            replace(selection, compose_files=(ROOT / "docker-compose.yml",))
        )
        captured.append(rendered)
        return check_wiring(selection, evaluation, rendered, reference)
    except ProfileError as exc:
        if exc.code == "ENV_FILES_UNCHECKED":
            return result(
                evaluation,
                [],
                [
                    *evaluation.pending_checks,
                    diagnostic(
                        "ENV_FILES_UNCHECKED",
                        "services",
                        "A service declares env_file; Compose rendering was not "
                        "attempted.",
                        "Ask deployment support to provide non-secret environment "
                        "evidence separately. Do not share file contents; offline "
                        "wiring remains unchecked.",
                    ),
                ],
                [],
            )
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
            "Use --profile-file PATH, followed by show or check.",
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
        help="show|check with --profile-file PATH",
    )
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    selection: ProfileSelection | None = None
    command = "show"
    try:
        options = parser.parse_args(args)
        tokens = options.selection
        if options.profile_file is None or len(tokens) != 1:
            raise ProfileError(
                "CLI_INPUT_INVALID", "Select a private profile file and command."
            )
        command = tokens[0]
        if command not in {"show", "check"}:
            raise ProfileError("CLI_INPUT_INVALID", "Choose show or check.")
        selection = select_deployment_profile(
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
                "registered_job_overrides",
                "station_model_database_state",
                "scientific_readiness",
                "actual_image_identity",
                "provider_access",
                "build_credentials",
            ),
        )
        code = 1
    support: dict[str, Any] = {"selection": None, "desired_source_revision": None}
    if selection is not None:
        support["selection"] = {
            "profile": "private-profile",
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
        "offline_validation": "bounded_standard_stored_data_forecast_and_backup",
        "pass_meaning": "configuration_and_service_wiring_consistency_only",
        "support": support,
        **asdict(evaluation),
    }
    if json_output:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        sys.stdout.write(
            f"{evaluation.summary}: desired configuration, unverified against host.\n"
            "PASS_OFFLINE means configuration and service-wiring consistency only.\n"
            "Runtime readiness and launch approval require separate evidence.\n"
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
                f"{item.field} = {value} ({item.source}; {item.operation}; "
                f"default={item.default_status})\n"
            )
        sys.stdout.write(
            "Unverified runtime: " + ", ".join(evaluation.unverified_runtime) + "\n"
        )
        sys.stdout.write("Reference: docs/deployment/profiles.md\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
