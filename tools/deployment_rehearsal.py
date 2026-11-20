#!/usr/bin/env python3
"""Preview, run or recover a disposable profile rehearsal."""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol

from sapphire_flow.config._overlay import load_merged_toml
from sapphire_flow.config.deployment_identity import parse_deployment_identity_config
from sapphire_flow.config.deployment_profile import (
    EvaluationSummary,
    ProfileError,
    ProfileEvaluation,
    ProfileSelection,
    evaluate_deployment_profile,
    select_deployment_profile,
)
from sapphire_flow.config.onboarding import parse_onboarding_config
from sapphire_flow.exceptions import ConfigurationError

try:
    from tools import deployment_profile as checker
    from tools import image_identity_probe as image_probe
except ModuleNotFoundError:
    import deployment_profile as checker
    import image_identity_probe as image_probe

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from types import FrameType

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 8 * 1024 * 1024
IdentityKind = Literal["release-target", "ci-development"]
_ROLES = frozenset(
    {
        "db_password",
        "sapphire_api_db_password",
        "sapphire_worker_db_password",
        "sapphire_backup_db_password",
        "access_token_pepper",
    }
)
_TRACKED_BINDS = {
    "/docker-entrypoint-initdb.d/init-db.sh": "docker/init-db.sh",
    "/etc/caddy/Caddyfile": "Caddyfile",
}
_LABEL = "org.sapphire.rehearsal"
_SYMBOL = "unresolved-rehearsal"
_PHASES = (
    "recheck inputs and probe preloaded images",
    "create private recovery record and fresh resources",
    "start database and Prefect; create and verify paused pools",
    "run normal init; verify two jobs and pause schedules",
    "start workers and API; verify internal authentication without flow execution",
    "remove positively owned resources and private generated files",
)
_CHANGES = (
    "replace project/resource names and add ownership labels",
    "use fresh named data volumes preserving sharing and access",
    "replace five runtime credential deliveries with generated test files",
    "pin preloaded immutable image IDs; remove builds and build secrets",
    "make both networks internal; remove all published ports; Caddy HTTP :80",
    "disable restarts; bind exact nonsecret snapshots read-only",
)
_RUNTIME = (
    "immutable image IDs and prepared release evidence",
    "local Unix-socket Docker endpoint and resource collisions",
    "generated project names, credential files and snapshot paths",
    "paused pools, init, worker heartbeats, internal API authentication and cleanup",
    "host ports, real credentials, forecasts and scientific readiness "
    "remain unverified",
)


@dataclass(frozen=True, kw_only=True, slots=True)
class FrozenInput:
    path: Path
    content: bytes = field(repr=False)
    mode: int
    identity: tuple[int, int]


@dataclass(frozen=True, kw_only=True, slots=True)
class Preview:
    status: Literal["PLAN_READY", "FAIL", "PENDING"]
    codes: tuple[str, ...]
    offline: ProfileEvaluation | None = None
    revision: str | None = None
    identity_kind: IdentityKind = "release-target"


def refuse(code: str) -> NoReturn:
    raise ProfileError(code, "Rehearsal preview refused; see the rehearsal guide.")


def guard_path(path: Path, secrets: Sequence[Path] = ()) -> None:
    try:
        resolved = path.resolve()
        if any(
            p.name == ".env" or p.name.startswith(".env.") for p in (path, resolved)
        ):
            refuse("PROTECTED_INPUT")
        for secret in secrets:
            if resolved == secret.resolve() or (
                path.exists() and secret.exists() and path.samefile(secret)
            ):
                refuse("PROTECTED_INPUT")
    except OSError:
        refuse("INPUT_UNREADABLE")


def capture(paths: Sequence[Path], secrets: Sequence[Path]) -> tuple[FrozenInput, ...]:
    # Check every alias before opening any candidate input.
    for path in paths:
        guard_path(path, secrets)
    snapshots: list[FrozenInput] = []
    total = 0
    try:
        for path in dict.fromkeys(paths):
            before = path.stat()
            if not path.is_file() or before.st_size > LIMIT:
                refuse("INPUT_UNSUPPORTED")
            with path.open("rb") as stream:
                content = stream.read(LIMIT + 1)
            after = path.stat()
            total += len(content)
            if total > LIMIT:
                refuse("INPUT_UNSUPPORTED")
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_mode,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_mode,
            ):
                refuse("INPUT_DRIFT")
            snapshots.append(
                FrozenInput(
                    path=path,
                    content=content,
                    mode=before.st_mode & 0o777,
                    identity=(before.st_dev, before.st_ino),
                )
            )
    except OSError:
        refuse("INPUT_UNREADABLE")
    return tuple(snapshots)


def verify_inputs(snapshots: tuple[FrozenInput, ...], secrets: Sequence[Path]) -> None:
    if capture(tuple(item.path for item in snapshots), secrets) != snapshots:
        refuse("INPUT_DRIFT")


def git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=root,
            env={
                "PATH": os.defpath,
                "HOME": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
            capture_output=True,
            timeout=10,
            check=True,
        )
        if len(completed.stdout) > LIMIT:
            refuse("SOURCE_UNAVAILABLE")
        return completed.stdout.decode().strip()
    except (OSError, UnicodeError, subprocess.SubprocessError):
        refuse("SOURCE_UNAVAILABLE")


def source_identity(selection: ProfileSelection, kind: IdentityKind, root: Path) -> str:
    installation = selection.installation
    if (
        installation is None
        or installation.project_directory.resolve() != root.resolve()
    ):
        refuse("SOURCE_CHECKOUT_MISMATCH")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        refuse("SOURCE_DIRTY")
    revision = git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        refuse("SOURCE_UNAVAILABLE")
    environment = dict(installation.environment)
    expected = (
        "0.dev0+g" + revision
        if kind == "ci-development"
        else (root / "RELEASE_TARGET").read_text().strip()
    )
    if (
        environment.get("SAPPHIRE_SOURCE_REVISION") != revision
        or environment.get("SAPPHIRE_RELEASE_VERSION") != expected
    ):
        refuse("SOURCE_IDENTITY_MISMATCH")
    tag = environment.get("VERSION", "")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag) or (
        kind == "release-target" and tag != expected
    ):
        refuse("SOURCE_IDENTITY_MISMATCH")
    return revision


def restrict_recipe(
    selection: ProfileSelection, recipe: dict[str, Any], root: Path
) -> dict[str, str]:
    installation = selection.installation
    if installation is None:
        refuse("INSTALLATION_REQUIRED")
    if selection.profile.inputs or selection.profile.scopes:
        refuse("REHEARSAL_SCOPE_UNSUPPORTED")
    raw = load_merged_toml(
        selection.profile.base_config, list(selection.profile.overlays), environment={}
    )
    identity = parse_deployment_identity_config(raw)
    onboarding = parse_onboarding_config(raw)
    if (
        identity.global_admin
        or len(identity.writable_tenants) != 1
        or onboarding is None
        or onboarding.tenant_code not in identity.writable_tenants
    ):
        refuse("REHEARSAL_TENANT_UNSUPPORTED")
    if frozenset(dict(installation.secret_bindings)) != _ROLES:
        refuse("REHEARSAL_SECRET_ROLES_UNSUPPORTED")
    if set(recipe) - {
        "name",
        "services",
        "volumes",
        "networks",
        "secrets",
        "x-app-build",
    }:
        refuse("REHEARSAL_RECIPE_UNSUPPORTED")
    for category in ("volumes", "networks"):
        for name, definition in recipe.get(category, {}).items():
            allowed = {"name"} if category == "volumes" else {"name", "driver", "ipam"}
            if (
                set(definition) - allowed
                or definition.get("ipam", {}) != {}
                or definition.get("name") != installation.project_name + "_" + name
            ):
                refuse("REHEARSAL_RESOURCE_UNSUPPORTED")
    files = {
        str(path.resolve()): f"input-{index}"
        for index, path in enumerate(
            (selection.profile.base_config, *selection.profile.overlays)
        )
    }
    for relative in _TRACKED_BINDS.values():
        path = root / relative
        if git(root, "ls-files", "--error-unmatch", "--", relative) != relative:
            refuse("REHEARSAL_BIND_UNSUPPORTED")
        files[str(path.resolve())] = relative.replace("/", "-")
    for service in recipe["services"].values():
        for mount in service.get("volumes", []):
            if mount["type"] in {"volume", "tmpfs"}:
                continue
            path = str(Path(mount.get("source", "")).resolve())
            target = mount.get("target")
            standard = target in _TRACKED_BINDS and path == str(
                (root / _TRACKED_BINDS[target]).resolve()
            )
            if (
                mount["type"] != "bind"
                or path not in files
                or (not standard and not mount.get("read_only"))
            ):
                refuse("REHEARSAL_BIND_UNSUPPORTED")
    return files


@dataclass(frozen=True, kw_only=True, slots=True)
class RecipeBindings:
    project: str = _SYMBOL
    run_id: str = _SYMBOL
    workspace: Path = Path("/__unresolved_rehearsal__")
    images: tuple[tuple[str, str], ...] = ()

    def image(self, source: str, recipe: dict[str, Any]) -> str:
        if self.images:
            return dict(self.images)[source]
        index = sorted({item["image"] for item in recipe["services"].values()}).index(
            source
        )
        return "${REHEARSAL_IMAGE_ID_" + str(index) + "}"


@dataclass(frozen=True, kw_only=True, slots=True)
class Prepared:
    preview: Preview
    recipe: dict[str, Any] | None = field(default=None, repr=False)
    frozen: tuple[FrozenInput, ...] = field(default=(), repr=False)
    snapshots: tuple[tuple[str, str], ...] = ()


_PREVIEW_BINDINGS = RecipeBindings()


def transform_recipe(
    recipe: dict[str, Any],
    snapshots: Mapping[str, str],
    bindings: RecipeBindings = _PREVIEW_BINDINGS,
) -> dict[str, Any]:
    """Private symbolic recipe; never executable preview output."""
    transformed = copy.deepcopy(recipe)
    transformed["name"] = bindings.project
    transformed.pop("x-app-build", None)
    for category in ("volumes", "networks"):
        for name, definition in transformed[category].items():
            definition["name"] = bindings.project + "_" + name
            definition["labels"] = {_LABEL: bindings.run_id}
            if category == "networks":
                definition["internal"] = True
    transformed["secrets"] = {
        name: {
            "file": str(bindings.workspace / "secrets" / name).replace("$", "$$"),
            "name": bindings.project + "_" + name,
        }
        for name in sorted(_ROLES)
    }
    for name, service in transformed["services"].items():
        service.pop("build", None)
        service["image"] = bindings.image(service["image"], recipe)
        service["labels"] = {**service.get("labels", {}), _LABEL: bindings.run_id}
        service.pop("ports", None)
        service["restart"] = "no"
        if name == "caddy":
            service["environment"]["SAPPHIRE_DOMAIN"] = ":80"
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                mount["source"] = (
                    str(bindings.workspace / "inputs").replace("$", "$$")
                    + "/"
                    + snapshots[str(Path(mount["source"]).resolve())]
                )
                mount["read_only"] = True
                mount["bind"] = {**mount.get("bind", {}), "create_host_path": False}
    return transformed


def verify_transform(
    original: dict[str, Any],
    candidate: dict[str, Any],
    snapshots: Mapping[str, str],
    bindings: RecipeBindings = _PREVIEW_BINDINGS,
    *,
    representation: Literal["executable", "rendered"] = "executable",
) -> None:
    restored = copy.deepcopy(candidate)

    def restore(
        target: dict[str, Any], source: dict[str, Any], key: str, expected: Any
    ) -> None:
        if target.get(key) != expected:
            refuse("TRANSFORM_INVALID")
        if key in source:
            target[key] = copy.deepcopy(source[key])
        else:
            target.pop(key, None)

    try:
        restore(restored, original, "name", bindings.project)
        restore(restored, original, "x-app-build", None)
        expected_secrets = {
            name: {
                "file": str(bindings.workspace / "secrets" / name).replace("$", "$$"),
                "name": bindings.project + "_" + name,
            }
            for name in _ROLES
        }
        restore(restored, original, "secrets", expected_secrets)
        for category in ("networks", "volumes"):
            for name, source in original[category].items():
                target = restored[category][name]
                restore(target, source, "name", bindings.project + "_" + name)
                restore(target, source, "labels", {_LABEL: bindings.run_id})
                if category == "networks":
                    restore(target, source, "internal", True)
        for name, source in original["services"].items():
            target = restored["services"][name]
            restore(target, source, "build", None)
            restore(target, source, "image", bindings.image(source["image"], original))
            restore(target, source, "ports", None)
            restore(target, source, "restart", "no")
            restore(
                target,
                source,
                "labels",
                {**source.get("labels", {}), _LABEL: bindings.run_id},
            )
            if name == "caddy":
                restore(
                    target["environment"],
                    source["environment"],
                    "SAPPHIRE_DOMAIN",
                    ":80",
                )
            for before, after in zip(
                source.get("volumes", []), target.get("volumes", []), strict=True
            ):
                if before["type"] == "bind":
                    expected_path = (
                        str(bindings.workspace / "inputs").replace("$", "$$")
                        + "/"
                        + snapshots[str(Path(before["source"]).resolve())]
                    )
                    restore(after, before, "source", expected_path)
                    restore(after, before, "read_only", True)
                    # Compose 2.38 omits false in its normalized bind mapping.
                    if representation == "rendered" and isinstance(
                        after.get("bind"), dict
                    ):
                        after["bind"].setdefault("create_host_path", False)
                    restore(
                        after,
                        before,
                        "bind",
                        {**before.get("bind", {}), "create_host_path": False},
                    )
    except (KeyError, TypeError, ValueError):
        refuse("TRANSFORM_INVALID")
    if restored != original:
        refuse("TRANSFORM_INVALID")


def prepare(
    selection: ProfileSelection,
    *,
    identity_kind: IdentityKind = "release-target",
    root: Path = ROOT,
) -> Prepared:
    installation = selection.installation
    if installation is None:
        refuse("INSTALLATION_REQUIRED")
    secrets = tuple(path for _, path in installation.secret_bindings)
    profile = selection.profile
    if profile.inputs or profile.scopes:
        refuse("REHEARSAL_SCOPE_UNSUPPORTED")
    paths = (
        profile.manifest_path,
        installation.source_path,
        profile.base_config,
        *profile.overlays,
        *selection.compose_files,
        root / "docker-compose.yml",
        root / "RELEASE_TARGET",
        *(root / item for item in _TRACKED_BINDS.values()),
        *((profile.runbook,) if profile.runbook else ()),
    )
    frozen = capture(paths, secrets)
    config_paths = {profile.base_config, *profile.overlays}
    if any(b"${" in item.content for item in frozen if item.path in config_paths):
        refuse("DYNAMIC_CONFIG_UNSUPPORTED")
    environment = dict(installation.environment)
    evaluated = evaluate_deployment_profile(selection, environment=environment)
    checked = checker.inspect_profile(selection, evaluated)
    verify_inputs(frozen, secrets)
    if checked.evaluation.summary != EvaluationSummary.PASS_OFFLINE:
        return Prepared(
            preview=Preview(
                status="FAIL"
                if checked.evaluation.summary == EvaluationSummary.FAIL
                else "PENDING",
                codes=tuple(
                    item.code
                    for item in (
                        *checked.evaluation.diagnostics,
                        *checked.evaluation.pending_checks,
                    )
                ),
                offline=checked.evaluation,
                identity_kind=identity_kind,
            )
        )

    if checked.recipe is None:
        refuse("RECIPE_EVIDENCE_MISSING")
    revision = source_identity(selection, identity_kind, root)
    inputs = restrict_recipe(selection, checked.recipe, root)
    transformed = transform_recipe(checked.recipe, inputs)
    verify_transform(checked.recipe, transformed, inputs)
    verify_inputs(frozen, secrets)
    if source_identity(selection, identity_kind, root) != revision:
        refuse("SOURCE_DRIFT")
    return Prepared(
        preview=Preview(
            status="PLAN_READY",
            codes=(),
            offline=checked.evaluation,
            revision=revision,
            identity_kind=identity_kind,
        ),
        recipe=checked.recipe,
        frozen=frozen,
        snapshots=tuple(inputs.items()),
    )


def preview(
    selection: ProfileSelection,
    *,
    identity_kind: IdentityKind = "release-target",
    root: Path = ROOT,
) -> Preview:
    return prepare(selection, identity_kind=identity_kind, root=root).preview


# Runtime stays separate from preview: no Docker calls occur during preparation.
_SOCKET = "unix:///var/run/docker.sock"
_PROJECT_LABEL = "com.docker.compose.project"
_SERVICES = (
    "postgres",
    "prefect-server",
    "init",
    "api",
    "caddy",
    "prefect-worker",
    "prefect-worker-ingest",
    "prefect-worker-backup",
)
_VOLUMES = (
    "pgdata",
    "prefect_data",
    "caddy_data",
    "caddy_config",
    "model_artifacts",
    "backups",
    "nwp_grids",
    "bafu_forecast_archive",
    "bafu_observation_archive",
)
_PROBES = ("default", "aquacast", "pools", "jobs", "workers")


class RehearsalRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class CommandOutput:
    stdout: str = field(repr=False)
    stderr: str = field(repr=False)
    returncode: int


class Executor(Protocol):
    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        timeout: float,
        stdin: str | None = None,
    ) -> CommandOutput: ...


def execute(
    command: list[str],
    environment: dict[str, str],
    timeout: float,
    stdin: str | None = None,
) -> CommandOutput:
    deadline = time.monotonic() + timeout
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    payload = (stdin or "").encode()
    if len(payload) > LIMIT:
        raise RehearsalRuntimeError("command_input_limit")
    try:
        with subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        ) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    if (
                        process.stdin is None
                        or process.stdout is None
                        or process.stderr is None
                    ):
                        raise RehearsalRuntimeError("command_pipes_missing")
                    for name, stream in (
                        ("stdout", process.stdout),
                        ("stderr", process.stderr),
                    ):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ, name)
                    if payload:
                        os.set_blocking(process.stdin.fileno(), False)
                        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                    else:
                        process.stdin.close()
                    offset = 0
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise RehearsalRuntimeError("command_timeout")
                        for key, _ in selector.select(min(remaining, 0.25)):
                            if key.data == "stdin":
                                offset += os.write(
                                    key.fd, payload[offset : offset + 65536]
                                )
                                if offset == len(payload):
                                    selector.unregister(key.fileobj)
                                    process.stdin.close()
                            else:
                                chunk = os.read(key.fd, 65536)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                else:
                                    buffers[key.data].extend(chunk)
                                    if sum(map(len, buffers.values())) > LIMIT:
                                        raise RehearsalRuntimeError(
                                            "command_output_limit"
                                        )
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                return CommandOutput(
                    stdout=buffers["stdout"].decode().strip(),
                    stderr=buffers["stderr"].decode().strip(),
                    returncode=process.returncode or 0,
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise RehearsalRuntimeError("command_unavailable_or_timeout") from None


@dataclass(frozen=True, kw_only=True, slots=True)
class Recovery:
    run_id: str
    daemon: str
    endpoint: str = _SOCKET
    known: tuple[tuple[str, str], ...] = ()

    @property
    def project(self) -> str:
        return "sapphire-rehearsal-" + self.run_id

    def names(self, kind: str) -> tuple[str, ...]:
        if kind == "container":
            return tuple(
                self.project + "-" + name + "-1" for name in _SERVICES
            ) + tuple(self.project + "-probe-" + name for name in _PROBES)
        return tuple(
            self.project + "_" + name
            for name in (_VOLUMES if kind == "volume" else ("backend", "frontend"))
        )


def private_directory(path: Path) -> None:
    if (
        path.is_symlink()
        or not path.is_dir()
        or path.stat().st_mode & 0o077
        or path.stat().st_uid != os.getuid()
    ):
        raise RehearsalRuntimeError("private_directory_required")


def durable_json(path: Path, value: object) -> None:
    private_directory(path.parent)
    temporary = path.with_name(path.name + ".new")
    if temporary.is_symlink() or path.is_symlink():
        raise RehearsalRuntimeError("unsafe_record_path")
    if temporary.exists():
        metadata = temporary.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise RehearsalRuntimeError("unsafe_record_path")
        temporary.unlink()
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_recovery(path: Path, record: Recovery) -> None:
    durable_json(
        path / "recovery.json",
        {
            "schema_version": 1,
            "run_id": record.run_id,
            "project": record.project,
            "endpoint": record.endpoint,
            "daemon": record.daemon,
            "planned": {
                kind: record.names(kind) for kind in ("container", "network", "volume")
            },
            "known": record.known,
        },
    )


def read_recovery(path: Path) -> Recovery:
    private_directory(path)
    target = path / "recovery.json"
    if (
        target.is_symlink()
        or not stat.S_ISREG(target.stat().st_mode)
        or target.stat().st_uid != os.getuid()
        or target.stat().st_mode & 0o077
        or target.stat().st_size > 65536
    ):
        raise RehearsalRuntimeError("unsafe_recovery_record")
    raw = json.loads(target.read_text())
    if (
        set(raw)
        != {
            "schema_version",
            "run_id",
            "project",
            "endpoint",
            "daemon",
            "planned",
            "known",
        }
        or raw["schema_version"] != 1
        or not re.fullmatch(r"[0-9a-f]{32}", raw["run_id"])
        or raw["endpoint"] != _SOCKET
        or not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", raw["daemon"])
    ):
        raise RehearsalRuntimeError("unsafe_recovery_record")
    known = tuple(tuple(item) for item in raw["known"])
    if len(known) > 64 or any(
        len(item) != 2
        or item[0] not in {"container", "network", "volume"}
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item[1])
        for item in known
    ):
        raise RehearsalRuntimeError("unsafe_recovery_record")
    record = Recovery(run_id=raw["run_id"], daemon=raw["daemon"], known=known)
    if raw["project"] != record.project or raw["planned"] != {
        kind: list(record.names(kind)) for kind in ("container", "network", "volume")
    }:
        raise RehearsalRuntimeError("unsafe_recovery_record")
    return record


@dataclass
class Runtime:
    directory: Path
    deadline: float
    clock: Callable[[], float] = time.monotonic
    executor: Executor = execute
    docker: str = "docker"
    record: Recovery | None = None
    canaries: tuple[str, ...] = field(default=(), repr=False)
    output_failure: str | None = None
    failed_operation: str | None = None

    def command(
        self, arguments: list[str], *, stdin: str | None = None, timeout: float = 120
    ) -> str:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise RehearsalRuntimeError("rehearsal_timeout")
        environment = {
            "PATH": os.defpath,
            "HOME": str(self.directory),
            "DOCKER_CONFIG": str(self.directory / "docker-config"),
            "DOCKER_HOST": _SOCKET,
        }
        operation = "compose_operation"
        if arguments[1:3] == ["--host", _SOCKET]:
            verb = arguments[3] if len(arguments) > 3 else ""
            operation = (
                "docker_" + verb
                if verb
                in {
                    "info",
                    "inspect",
                    "image",
                    "container",
                    "network",
                    "volume",
                    "ps",
                    "run",
                    "exec",
                    "wait",
                    "logs",
                    "rm",
                    "kill",
                }
                else "docker_operation"
            )
        elif "config" in arguments:
            operation = "compose_config"
        elif "up" in arguments:
            operation = "compose_up"
        try:
            output = self.executor(
                arguments, environment, min(timeout, remaining), stdin
            )
        except Exception:
            self.failed_operation = operation
            raise
        if any(
            value in output.stdout or value in output.stderr for value in self.canaries
        ):
            self.failed_operation = operation
            self.output_failure = "secret_output_detected"
            raise RehearsalRuntimeError("secret_output_detected")
        if output.returncode:
            self.failed_operation = operation
            raise RehearsalRuntimeError("command_failed")
        return output.stdout

    def run(
        self, args: list[str], *, stdin: str | None = None, timeout: float = 120
    ) -> str:
        return self.command(
            [self.docker, "--host", _SOCKET, *args], stdin=stdin, timeout=timeout
        )

    def daemon(self) -> str:
        identity = self.run(["info", "--format", "{{.ID}}"])
        if not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", identity):
            raise RehearsalRuntimeError("daemon_identity_invalid")
        return identity

    def guard_daemon(self) -> Recovery:
        if (
            self.record is None
            or self.record.endpoint != _SOCKET
            or self.daemon() != self.record.daemon
        ):
            raise RehearsalRuntimeError("daemon_changed")
        return self.record

    def inspect_owned(self, kind: str, identifier: str) -> dict[str, Any]:
        record = self.guard_daemon()
        raw = json.loads(self.run([kind, "inspect", identifier]))
        if len(raw) != 1:
            raise RehearsalRuntimeError("resource_identity_invalid")
        item = raw[0]
        labels = (
            item.get("Config", {}).get("Labels", {})
            if kind == "container"
            else item.get("Labels", {})
        )
        if (
            labels.get(_LABEL) != record.run_id
            or labels.get(_PROJECT_LABEL) != record.project
            or item.get("Name", "").lstrip("/") not in record.names(kind)
        ):
            raise RehearsalRuntimeError("foreign_resource_refused")
        return item

    def inventory(self) -> dict[str, list[str]]:
        record = self.guard_daemon()
        result: dict[str, list[str]] = {}
        for kind, command in (
            ("container", ["ps", "-aq"]),
            ("network", ["network", "ls", "-q"]),
            ("volume", ["volume", "ls", "-q"]),
        ):
            identifiers = self.run(
                [*command, "--filter", "label=" + _PROJECT_LABEL + "=" + record.project]
            ).splitlines()
            if len(identifiers) > len(record.names(kind)):
                raise RehearsalRuntimeError("resource_inventory_overflow")
            # Recover objects after a lost label as well as after interrupted ID
            # persistence. A changed label is a refusal, never evidence of absence.
            for name in record.names(kind):
                identifiers.extend(
                    self.run([*command, "--filter", "name=^" + name + "$"]).splitlines()
                )
            for known_kind, identifier in record.known:
                if known_kind == kind:
                    selector = (
                        "id=" + identifier
                        if kind in {"container", "network"}
                        else "name=^" + identifier + "$"
                    )
                    identifiers.extend(
                        self.run([*command, "--filter", selector]).splitlines()
                    )
            identifiers = list(dict.fromkeys(identifiers))
            for identifier in identifiers:
                self.inspect_owned(kind, identifier)
            result[kind] = identifiers
        return result

    def checkpoint(self) -> None:
        inventory = self.inventory()
        record = self.guard_daemon()
        self.record = Recovery(
            run_id=record.run_id,
            daemon=record.daemon,
            known=tuple(
                (kind, identifier)
                for kind, identifiers in inventory.items()
                for identifier in identifiers
            ),
        )
        save_recovery(self.directory, self.record)

    def collision_check(self) -> None:
        record = self.guard_daemon()
        for kind, command in (
            ("container", ["ps", "-aq"]),
            ("network", ["network", "ls", "-q"]),
            ("volume", ["volume", "ls", "-q"]),
        ):
            if self.run(
                [*command, "--filter", "label=" + _PROJECT_LABEL + "=" + record.project]
            ):
                raise RehearsalRuntimeError("resource_collision")
            for name in record.names(kind):
                if self.run([*command, "--filter", "name=^" + name + "$"]):
                    raise RehearsalRuntimeError("resource_collision")

    def service(self, name: str) -> str:
        record = self.guard_daemon()
        identifiers = self.run(
            [
                "ps",
                "-aq",
                "--filter",
                "label=" + _PROJECT_LABEL + "=" + record.project,
                "--filter",
                "label=com.docker.compose.service=" + name,
            ]
        ).splitlines()
        if len(identifiers) != 1:
            raise RehearsalRuntimeError("service_inventory_invalid")
        self.inspect_owned("container", identifiers[0])
        return identifiers[0]

    def probe(self, name: str, args: list[str], *, stdin: str | None = None) -> str:
        record = self.guard_daemon()
        if name not in _PROBES or args[0] != "run":
            raise RehearsalRuntimeError("probe_refused")
        try:
            return self.run(
                [
                    "run",
                    "--name",
                    record.project + "-probe-" + name,
                    "--label",
                    _LABEL + "=" + record.run_id,
                    "--label",
                    _PROJECT_LABEL + "=" + record.project,
                    *args[1:],
                ],
                stdin=stdin,
            )
        finally:
            # The predeclared name and labels recover a probe interrupted before
            # Docker returned its ID; checkpoint is best-effort during failure.
            self.checkpoint()


def erase_private_inputs(path: Path) -> None:
    private_directory(path)
    for name in ("recipe.json", "recipe.json.new", "images.json", "images.json.new"):
        target = path / name
        if target.is_symlink():
            raise RehearsalRuntimeError("unsafe_generated_file")
        target.unlink(missing_ok=True)
    for name in ("secrets", "inputs"):
        directory = path / name
        if not directory.exists() and not directory.is_symlink():
            continue
        private_directory(directory)
        for child in directory.iterdir():
            if (
                child.is_symlink()
                or not child.is_file()
                or (name == "secrets" and child.name not in _ROLES)
                or (
                    name == "inputs"
                    and not re.fullmatch(
                        r"input-[0-9]+|Caddyfile|docker-init-db.sh", child.name
                    )
                )
            ):
                raise RehearsalRuntimeError("unsafe_generated_file")
            child.unlink()
        directory.rmdir()


def cleanup_runtime(runtime: Runtime) -> dict[str, int]:
    counts = {"container": 0, "network": 0, "volume": 0}
    try:
        inventory = runtime.inventory()
        for kind in ("container", "network", "volume"):
            for identifier in inventory[kind]:
                runtime.inspect_owned(kind, identifier)
                if kind == "container" and runtime.canaries:
                    try:
                        runtime.run(["logs", identifier])
                    except RehearsalRuntimeError:
                        runtime.output_failure = (
                            runtime.output_failure or "container_output_unverified"
                        )
                runtime.run(
                    [
                        kind,
                        "rm",
                        *(["--force"] if kind == "container" else []),
                        identifier,
                    ]
                )
                counts[kind] += 1
        if any(runtime.inventory().values()):
            raise RehearsalRuntimeError("cleanup_incomplete")
        runtime.checkpoint()
        return counts
    finally:
        erase_private_inputs(runtime.directory)


class ProbeRunner:
    def __init__(self, runtime: Runtime, name: str) -> None:
        self.runtime, self.name = runtime, name

    def run(self, args: list[str], *, stdin: str | None = None) -> str:
        if args[0] == "run":
            return self.runtime.probe(self.name, args, stdin=stdin)
        return self.runtime.run(args, stdin=stdin)


def resolve_images(
    runtime: Runtime, recipe: dict[str, Any], environment: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    images: dict[str, str] = {}
    variants: tuple[tuple[Literal["default", "aquacast"], str], ...] = (
        ("default", "api"),
        ("aquacast", "prefect-worker"),
    )
    for variant, service in variants:
        tag = recipe["services"][service]["image"]
        probe = image_probe.probe_image(
            tag,
            environment["SAPPHIRE_RELEASE_VERSION"],
            environment["SAPPHIRE_SOURCE_REVISION"],
            variant=variant,
            runner=ProbeRunner(runtime, variant),
        )
        images[tag] = probe.image_id
    for name in ("postgres", "prefect-server", "caddy"):
        tag = recipe["services"][name]["image"]
        if "@sha256:" not in tag:
            raise RehearsalRuntimeError("infrastructure_image_unpinned")
        info = json.loads(runtime.run(["image", "inspect", tag]))[0]
        identifier = info.get("Id", "")
        if (
            not re.fullmatch(r"sha256:[0-9a-f]{64}", identifier)
            or info.get("Os") != "linux"
        ):
            raise RehearsalRuntimeError("infrastructure_image_invalid")
        if name == "postgres" and info.get("Architecture") != "amd64":
            raise RehearsalRuntimeError("postgres_platform_mismatch")
        images[tag] = identifier
    return tuple(sorted(images.items()))


def write_inputs(
    prepared: Prepared, bindings: RecipeBindings, runtime: Runtime
) -> None:
    paths = dict(prepared.snapshots)
    (runtime.directory / "inputs").mkdir(mode=0o700)
    (runtime.directory / "secrets").mkdir(mode=0o700)
    for frozen in prepared.frozen:
        alias = paths.get(str(frozen.path.resolve()))
        if alias is None:
            continue
        target = runtime.directory / "inputs" / alias
        with target.open("xb") as stream:
            stream.write(frozen.content)
        target.chmod(0o444 | (frozen.mode & 0o111))
    canaries: list[str] = []
    for name in sorted(_ROLES):
        value = secrets.token_hex(32)
        canaries.append(value)
        target = runtime.directory / "secrets" / name
        with target.open("x") as stream:
            stream.write(value + "\n")
        target.chmod(0o444)
    runtime.canaries = tuple(canaries)
    if prepared.recipe is None:
        raise RehearsalRuntimeError("recipe_evidence_missing")
    recipe = transform_recipe(prepared.recipe, paths, bindings)
    verify_transform(prepared.recipe, recipe, paths, bindings)
    durable_json(runtime.directory / "recipe.json", recipe)


def compose_command(runtime: Runtime, executable: str) -> list[str]:
    record = runtime.guard_daemon()
    return [
        executable,
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(runtime.directory),
        "--project-name",
        record.project,
        "-f",
        str(runtime.directory / "recipe.json"),
    ]


def render_runtime_recipe(
    runtime: Runtime, executable: str, prepared: Prepared, bindings: RecipeBindings
) -> None:
    rendered = json.loads(
        runtime.command(
            [
                *compose_command(runtime, executable),
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ]
        )
    )
    if prepared.recipe is None:
        raise RehearsalRuntimeError("recipe_evidence_missing")
    verify_transform(
        prepared.recipe,
        rendered,
        dict(prepared.snapshots),
        bindings,
        representation="rendered",
    )


def helper(runtime: Runtime, image: str, action: str) -> None:
    record = runtime.guard_daemon()
    if action == "api":
        identifier = runtime.service("api")
        output = runtime.run(
            [
                "exec",
                "-e",
                "SAPPHIRE_REHEARSAL_RUN_ID=" + record.run_id,
                "-e",
                "PREFECT_HOME=/tmp/prefect",
                identifier,
                "/entrypoint.sh",
                "python",
                "-m",
                "sapphire_flow.cli.deployment_rehearsal",
                "api",
            ]
        )
    else:
        name = {"prepare-pools": "pools", "pause-jobs": "jobs", "workers": "workers"}[
            action
        ]
        output = runtime.probe(
            name,
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                record.project + "_backend",
                "--user",
                "app",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--entrypoint",
                "/app/.venv/bin/python",
                "-e",
                "SAPPHIRE_REHEARSAL_RUN_ID=" + record.run_id,
                "-e",
                "PREFECT_API_URL=http://prefect-server:4200/api",
                "-e",
                "PREFECT_HOME=/tmp/prefect",
                image,
                "-m",
                "sapphire_flow.cli.deployment_rehearsal",
                action,
            ],
        )
    checked = json.loads(output)
    if checked != {"status": "ok", "stage": action}:
        code = checked.get("code")
        if code in {
            "unexpected_flow_execution",
            "workers_not_ready",
            "flow_inventory_overflow",
            "flow_history_overflow",
        }:
            raise RehearsalRuntimeError(code)
        raise RehearsalRuntimeError("helper_verification_failed")


def wait_services(
    runtime: Runtime, names: tuple[str, ...], sleep: Callable[[float], None]
) -> None:
    while True:
        ready = True
        for name in names:
            item = runtime.inspect_owned("container", runtime.service(name))
            state = item["State"]
            if state["Status"] in {"dead", "exited"}:
                raise RehearsalRuntimeError("isolated_startup_failed")
            if state.get("Health", {}).get("Status") != "healthy":
                ready = False
        if ready:
            return
        if runtime.deadline - runtime.clock() < 2:
            raise RehearsalRuntimeError("isolated_startup_failed")
        sleep(1)


def verify_database(runtime: Runtime, recipe: dict[str, Any], tenant: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    expected = ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_current_head()
    if expected is None or not re.fullmatch(r"[A-Za-z0-9_]+", expected):
        raise RehearsalRuntimeError("migration_head_invalid")
    user = recipe["services"]["postgres"]["environment"]["POSTGRES_USER"]
    sql = """SELECT CASE WHEN
        (SELECT count(*) FROM alembic_version WHERE version_num = :'head') = 1
        AND (SELECT count(*) FROM alembic_version) = 1
        AND (SELECT count(*) FROM tenants WHERE code = :'tenant') = 1
        AND (SELECT count(*) FROM pg_roles
             WHERE rolname IN ('sapphire_api', 'sapphire_worker', 'sapphire_backup')
             AND rolcanlogin) = 3
        THEN 'ok' ELSE 'failed' END;
"""
    output = runtime.run(
        [
            "exec",
            "-i",
            "--user",
            "postgres",
            runtime.service("postgres"),
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "head=" + expected,
            "-v",
            "tenant=" + tenant,
            "-U",
            user,
            "-d",
            "sapphire",
            "-At",
        ],
        stdin=sql,
    )
    if output != "ok":
        raise RehearsalRuntimeError("database_bootstrap_invalid")


def start_and_verify(
    runtime: Runtime,
    executable: str,
    prepared: Prepared,
    bindings: RecipeBindings,
    tenant: str,
    sleep: Callable[[float], None],
    mark: Callable[[str], None],
    checks: list[str],
) -> None:
    if prepared.recipe is None:
        raise RehearsalRuntimeError("recipe_evidence_missing")
    command = compose_command(runtime, executable)

    def up(*services: str) -> None:
        runtime.command(
            [
                *command,
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                *services,
            ]
        )
        runtime.checkpoint()

    up("postgres")
    wait_services(runtime, ("postgres",), sleep)
    up("prefect-server")
    wait_services(runtime, ("prefect-server",), sleep)
    checks.append("database_prefect_health")
    image = dict(bindings.images)[prepared.recipe["services"]["api"]["image"]]
    helper(runtime, image, "prepare-pools")
    checks.append("paused_pools_before_init")
    mark("init")
    up("init")
    identifier = runtime.service("init")
    if (
        runtime.run(["wait", identifier], timeout=runtime.deadline - runtime.clock())
        != "0"
    ):
        raise RehearsalRuntimeError("init_failed")
    verify_database(runtime, prepared.recipe, tenant)
    checks.append("migration_tenant_roles")
    helper(runtime, image, "pause-jobs")
    checks.append("jobs_schedules_pools_paused")
    up(
        "api",
        "caddy",
        "prefect-worker",
        "prefect-worker-ingest",
        "prefect-worker-backup",
    )
    wait_services(runtime, ("api", "caddy"), sleep)
    while True:
        try:
            helper(runtime, image, "workers")
            break
        except RehearsalRuntimeError as exc:
            if (
                str(exc) != "workers_not_ready"
                or runtime.deadline - runtime.clock() < 2
            ):
                raise
            sleep(1)
    checks.append("worker_heartbeats")
    helper(runtime, image, "api")
    checks.append("internal_api_health_auth_empty_collection")
    helper(runtime, image, "workers")
    checks.append("no_execution")
    for name in ("prefect-worker", "prefect-worker-ingest", "prefect-worker-backup"):
        item = runtime.inspect_owned("container", runtime.service(name))
        if item.get("State", {}).get("Running") is not True:
            raise RehearsalRuntimeError("isolated_startup_failed")
    for identifier in runtime.inventory()["container"]:
        runtime.run(["logs", identifier])
        runtime.inspect_owned("container", identifier)


def runtime_report(
    status: str,
    code: str | None,
    record: Recovery | None,
    identity: IdentityKind,
    revision: str | None,
    stage: str,
    elapsed: float,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scope": "disposable_rehearsal",
        "status": status,
        "code": code,
        "run_id": record.run_id if record else None,
        "stage": stage,
        "identity_kind": identity,
        "source_revision": revision,
        "elapsed_seconds": round(elapsed, 3),
        "offline_status": "PASS_OFFLINE",
        "cleanup_counts": counts or {},
        "host_port_verified": False,
        "forecast_executed": None,
        "real_credentials_verified": False,
        "scientific_readiness": "unverified",
        "persistent_launch_approved": False,
    }


def compose_executable(selection: ProfileSelection) -> str:
    # Same fixed system discovery boundary as offline config, without invoking a
    # Docker wrapper or loading user Docker configuration.
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
    for executable in dict.fromkeys(item for item in candidates if item):
        try:
            output = checker._capture(  # pyright: ignore[reportPrivateUsage]
                [executable, "config", "--help"],
                {"PATH": os.defpath, "HOME": "/dev/null", "DOCKER_CONFIG": "/dev/null"},
                ROOT,
            )
        except ProfileError:
            continue
        if b"--no-env-resolution" in output:
            return executable
    raise RehearsalRuntimeError("compose_unavailable")


def run_rehearsal(
    selection: ProfileSelection,
    directory: Path,
    *,
    identity_kind: IdentityKind = "release-target",
    clock: Callable[[], float] = time.monotonic,
    executor: Executor = execute,
    run_id_source: Callable[[], str] = lambda: secrets.token_hex(16),
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, dict[str, Any]]:
    started = clock()
    prepared = prepare(selection, identity_kind=identity_kind)
    if (
        prepared.preview.status != "PLAN_READY"
        or prepared.recipe is None
        or selection.installation is None
    ):
        return 2, {
            "schema_version": 1,
            "scope": "disposable_rehearsal",
            "status": "REFUSED",
            "codes": prepared.preview.codes,
            "offline_status": prepared.preview.offline.summary
            if prepared.preview.offline
            else None,
        }
    if (
        not directory.is_absolute()
        or directory.exists()
        or directory.is_symlink()
        or directory == ROOT
        or ROOT in directory.parents
        or any(ord(char) < 32 for char in str(directory))
    ):
        raise RehearsalRuntimeError("new_external_report_directory_required")
    executable = compose_executable(selection)
    docker = shutil.which("docker")
    if docker is None:
        raise RehearsalRuntimeError("docker_unavailable")
    run_id = run_id_source()
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise RehearsalRuntimeError("run_identity_invalid")
    directory.mkdir(mode=0o700)
    private_directory(directory)
    (directory / "docker-config").mkdir(mode=0o700)
    runtime = Runtime(
        directory=directory,
        deadline=started + 900,
        clock=clock,
        executor=executor,
        docker=docker,
    )
    stage = "preflight"
    status, code = "FAIL_REHEARSAL", None
    counts: dict[str, int] = {}
    armed = False
    startup_started = False
    images: tuple[tuple[str, str], ...] = ()
    checks: list[str] = []
    cleanup_code: str | None = None
    cleanup_cause: str | None = None
    failure_operation: str | None = None
    cleanup_operation: str | None = None

    def mark(value: str) -> None:
        nonlocal stage
        stage = value
        durable_json(
            directory / "summary.json",
            runtime_report(
                "RUNNING",
                None,
                runtime.record,
                identity_kind,
                prepared.preview.revision,
                stage,
                clock() - started,
            ),
        )

    try:
        runtime.record = Recovery(run_id=run_id, daemon=runtime.daemon())
        runtime.collision_check()
        save_recovery(directory, runtime.record)
        armed = True
        mark("image_preflight")
        images = resolve_images(
            runtime, prepared.recipe, dict(selection.installation.environment)
        )
        bindings = RecipeBindings(
            project=runtime.record.project,
            run_id=run_id,
            workspace=directory,
            images=images,
        )
        verify_inputs(
            prepared.frozen,
            tuple(path for _, path in selection.installation.secret_bindings),
        )
        if source_identity(selection, identity_kind, ROOT) != prepared.preview.revision:
            raise RehearsalRuntimeError("source_drift")
        write_inputs(prepared, bindings, runtime)
        render_runtime_recipe(runtime, executable, prepared, bindings)
        durable_json(
            directory / "images.json",
            {
                "image_ids": sorted(set(dict(images).values())),
                "source_revision": prepared.preview.revision,
                "identity_kind": identity_kind,
            },
        )
        # The tenant is parsed from the frozen application snapshot, never a
        # changing operator file. Its value never enters report/log output.
        raw = load_merged_toml(
            directory
            / "inputs"
            / dict(prepared.snapshots)[str(selection.profile.base_config.resolve())],
            [
                directory / "inputs" / dict(prepared.snapshots)[str(path.resolve())]
                for path in selection.profile.overlays
            ],
            environment={},
        )
        tenant = next(iter(parse_deployment_identity_config(raw).writable_tenants))
        mark("startup")
        startup_started = True
        start_and_verify(
            runtime, executable, prepared, bindings, tenant, sleep, mark, checks
        )
        status = "PASS_REHEARSAL"
    except (
        RehearsalRuntimeError,
        ProfileError,
        image_probe.ImageIdentityProbeError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ConfigurationError,
        KeyboardInterrupt,
    ) as exc:
        if isinstance(exc, KeyboardInterrupt):
            code = "interrupted"
        elif isinstance(exc, RehearsalRuntimeError):
            code = str(exc)
        elif isinstance(exc, ProfileError):
            code = exc.code
        elif isinstance(exc, image_probe.ImageIdentityProbeError):
            code = "image_identity_invalid"
        else:
            code = "rehearsal_check_failed"
    except Exception:
        code = "unexpected_failure"
    finally:
        failure_operation = runtime.failed_operation
        runtime.failed_operation = None
        runtime.deadline = clock() + 120
        if armed:
            try:
                counts = cleanup_runtime(runtime)
            except Exception as exc:
                status, cleanup_code = "FAIL_REHEARSAL", "cleanup_incomplete"
                cleanup_operation = runtime.failed_operation
                cleanup_cause = (
                    str(exc)
                    if isinstance(exc, RehearsalRuntimeError)
                    else exc.code
                    if isinstance(exc, ProfileError)
                    else "unexpected_failure"
                )
        else:
            erase_private_inputs(directory)
    if runtime.output_failure:
        status, code = "FAIL_REHEARSAL", code or runtime.output_failure
    report = runtime_report(
        status,
        code,
        runtime.record,
        identity_kind,
        prepared.preview.revision,
        stage,
        clock() - started,
        counts,
    )
    report.update(
        {
            "failure_code": code,
            "failure_operation": failure_operation,
            "cleanup_operation": cleanup_operation,
            "cleanup_code": cleanup_code,
            "cleanup_cause": cleanup_cause,
            "code": cleanup_code or code,
            "verified_checks": checks,
            "image_ids": sorted(set(dict(images).values())),
            "output_failure": runtime.output_failure,
            "forecast_executed": True
            if code == "unexpected_flow_execution"
            else False
            if "no_execution" in checks
            else None,
        }
    )
    durable_json(directory / "summary.json", report)
    return (
        0 if status == "PASS_REHEARSAL" else 1 if startup_started or cleanup_code else 2
    ), report


def cleanup_rehearsal(
    directory: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
    executor: Executor = execute,
) -> tuple[int, dict[str, Any]]:
    started = clock()
    if not directory.is_absolute():
        raise RehearsalRuntimeError("absolute_report_directory_required")
    record = read_recovery(directory)
    docker = shutil.which("docker")
    if docker is None:
        raise RehearsalRuntimeError("docker_unavailable")
    runtime = Runtime(
        directory=directory,
        deadline=started + 120,
        clock=clock,
        executor=executor,
        docker=docker,
        record=record,
    )
    try:
        counts = cleanup_runtime(runtime)
        result = {
            "schema_version": 1,
            "scope": "disposable_rehearsal_cleanup",
            "status": "CLEANED",
            "run_id": record.run_id,
            "cleanup_counts": counts,
        }
        code = 0
    except Exception as exc:
        result = {
            "schema_version": 1,
            "scope": "disposable_rehearsal_cleanup",
            "status": "FAILED",
            "run_id": record.run_id,
            "code": "cleanup_incomplete",
            "cleanup_operation": runtime.failed_operation,
            "cleanup_cause": str(exc)
            if isinstance(exc, RehearsalRuntimeError)
            else exc.code
            if isinstance(exc, ProfileError)
            else "unexpected_failure",
        }
        code = 1
    durable_json(directory / "cleanup-summary.json", result)
    return code, result


_ACTIONS = {
    "image_identity_invalid": (
        "Prepare both local images for the exact selected source identity."
    ),
    "infrastructure_image_unpinned": "Use the checked pinned infrastructure recipe.",
    "postgres_platform_mismatch": "Prepare the required Linux amd64 Postgres image.",
    "resource_collision": (
        "Use a fresh report directory and run identity; never adopt resources."
    ),
    "compose_unavailable": "Install a supported standalone Compose renderer.",
    "docker_unavailable": (
        "Install the Docker client and use the dedicated test daemon."
    ),
    "init_failed": (
        "Check bootstrap configuration and image compatibility before retrying."
    ),
    "command_failed": "Check the reported startup stage and local image prerequisites.",
    "command_timeout": (
        "Check runner capacity before retrying within the startup budget."
    ),
    "cleanup_incomplete": (
        "Resolve the reported cause, then clean up using the saved report directory."
    ),
    "daemon_changed": (
        "Reconnect the original daemon; do not clean resources on a different daemon."
    ),
    "foreign_resource_refused": (
        "Inspect changed resource ownership manually; automatic deletion is refused."
    ),
    "unexpected_failure": (
        "Report the fixed failure code and stage; private output is not retained."
    ),
    "interrupted": "Check cleanup status before retrying the interrupted rehearsal.",
    "SOURCE_DIRTY": ("Use a clean checked checkout; do not bypass changed files."),
    "SOURCE_IDENTITY_MISMATCH": (
        "Align the selected release/source triple with this checkout and identity kind."
    ),
    "SOURCE_CHECKOUT_MISMATCH": (
        "Run from the same checkout selected by installation.project_directory."
    ),
    "SOURCE_UNAVAILABLE": ("Check that this is an accessible tracked Git checkout."),
    "SOURCE_DRIFT": ("Stabilize the source checkout and repeat preview."),
    "INPUT_DRIFT": ("Stabilize the selected input files and repeat preview."),
    "PROTECTED_INPUT": (
        "Keep nonsecret inputs separate from credential deliveries and "
        ".env files, including aliases."
    ),
    "INPUT_UNREADABLE": (
        "Check that all selected nonsecret files exist and are readable."
    ),
    "DYNAMIC_CONFIG_UNSUPPORTED": (
        "Supply literal application configuration for this rehearsal."
    ),
    "DOCKER_OVERRIDE_UNSUPPORTED": (
        "Unset Docker endpoint/context/TLS overrides for this command."
    ),
    "REHEARSAL_TENANT_UNSUPPORTED": (
        "Use exactly one writable tenant matching onboarding, without global admin."
    ),
    "REHEARSAL_BIND_UNSUPPORTED": (
        "Use standard named data volumes and read-only selected application files."
    ),
    "REHEARSAL_RESOURCE_UNSUPPORTED": (
        "Remove external resources and custom names from the rehearsal selection."
    ),
    "CLI_INPUT_INVALID": (
        "Use --profile-file PATH preview --installation PATH; see --help."
    ),
}


def action(code: str) -> str:
    if code in _ACTIONS:
        return _ACTIONS[code]
    if code in checker.SELECTION_GUIDANCE:
        return checker.SELECTION_GUIDANCE[code][1]
    return (
        "Run the profile checker for the same selection; "
        "consult the rehearsal support limits before retrying."
    )


@contextlib.contextmanager
def runtime_signals() -> Iterator[None]:
    def interrupt(signum: int, frame: FrameType | None) -> NoReturn:
        raise RehearsalRuntimeError("interrupted")

    previous = {
        name: signal.signal(name, interrupt)
        for name in (
            signal.SIGINT,
            signal.SIGTERM,
            *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else []),
        )
    }
    try:
        yield
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)


def print_runtime_result(
    code: int, report: dict[str, Any], json_output: bool, directory: Path | None
) -> int:
    if json_output:
        print(json.dumps(report, sort_keys=True))
    else:
        print(str(report["status"]) + ": disposable rehearsal only.")
        codes = list(report.get("codes", ()))
        codes.extend(
            report[key]
            for key in ("code", "failure_code", "cleanup_cause")
            if report.get(key)
        )
        for item in dict.fromkeys(codes):
            print("  " + item + ": " + action(item))
        if report.get("code") == "cleanup_incomplete" and directory is not None:
            print(
                "Recover: uv run python tools/deployment_rehearsal.py "
                "cleanup --report-dir " + shlex.quote(str(directory))
            )
        print(
            "Host ports, real credentials, forecasts and scientific readiness "
            "are unverified."
        )
    return code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        refuse("CLI_INPUT_INVALID")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_output = "json" in args or "--format=json" in args
    parser = Parser(description=__doc__)
    parser.add_argument("command", choices=("preview", "run", "cleanup"))
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--image-identity",
        choices=("release-target", "ci-development"),
        default="release-target",
    )
    identity_kind: IdentityKind = "release-target"
    command = "preview"
    directory: Path | None = None
    try:
        options = parser.parse_args(args)
        identity_kind = options.image_identity
        command, directory = options.command, options.report_dir
        # Only selectors are observed by the existing boundary; no credential env.
        if any(
            os.environ.get(name)
            for name in (
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            )
        ):
            refuse("DOCKER_OVERRIDE_UNSUPPORTED")
        if command == "cleanup":
            if (
                directory is None
                or options.profile_file is not None
                or options.installation is not None
            ):
                refuse("CLI_INPUT_INVALID")
            with runtime_signals():
                code, report = cleanup_rehearsal(directory)
            return print_runtime_result(code, report, json_output, directory)
        if (
            options.profile_file is None
            or options.installation is None
            or (command == "run" and directory is None)
            or (command == "preview" and directory is not None)
        ):
            refuse("CLI_INPUT_INVALID")
        for path in (options.profile_file, options.installation):
            guard_path(path)
        selectors = capture((options.profile_file, options.installation), ())
        selection = select_deployment_profile(
            profile_file=options.profile_file,
            installation_file=options.installation,
            ambient_environment=os.environ,
        )
        verify_inputs(
            selectors,
            tuple(path for _, path in selection.installation.secret_bindings)
            if selection.installation
            else (),
        )
        if command == "run":
            if directory is None:
                refuse("CLI_INPUT_INVALID")
            with runtime_signals():
                code, report = run_rehearsal(
                    selection, directory, identity_kind=identity_kind
                )
            return print_runtime_result(code, report, json_output, directory)
        outcome = preview(selection, identity_kind=options.image_identity)
    except RehearsalRuntimeError as exc:
        return print_runtime_result(
            1 if command == "cleanup" else 2,
            {
                "schema_version": 1,
                "scope": "disposable_rehearsal",
                "status": "REFUSED",
                "code": str(exc),
            },
            json_output,
            directory,
        )
    except ProfileError as exc:
        pending_codes = {
            "SOURCE_DIRTY",
            "SOURCE_UNAVAILABLE",
            "SOURCE_CHECKOUT_MISMATCH",
            "REHEARSAL_SCOPE_UNSUPPORTED",
            "REHEARSAL_TENANT_UNSUPPORTED",
            "REHEARSAL_SECRET_ROLES_UNSUPPORTED",
            "REHEARSAL_RECIPE_UNSUPPORTED",
            "REHEARSAL_RESOURCE_UNSUPPORTED",
            "REHEARSAL_BIND_UNSUPPORTED",
            "DYNAMIC_CONFIG_UNSUPPORTED",
            "DOCKER_OVERRIDE_UNSUPPORTED",
            "INSTALLATION_REQUIRED",
        }
        outcome = Preview(
            status="PENDING" if exc.code in pending_codes else "FAIL",
            codes=(exc.code,),
            identity_kind=identity_kind,
        )

    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
        ConfigurationError,
    ):
        outcome = Preview(
            status="FAIL", codes=("INPUT_INVALID",), identity_kind=identity_kind
        )
    except Exception:
        outcome = Preview(
            status="FAIL", codes=("unexpected_failure",), identity_kind=identity_kind
        )
    if command in {"run", "cleanup"}:
        return print_runtime_result(
            1 if command == "cleanup" else 2,
            {
                "schema_version": 1,
                "scope": "disposable_rehearsal",
                "status": "REFUSED",
                "codes": outcome.codes,
            },
            json_output,
            directory,
        )
    report = {
        "schema_version": 1,
        "scope": "disposable_rehearsal_preview",
        "status": outcome.status,
        "codes": outcome.codes,
        "actions": tuple(action(code) for code in outcome.codes),
        "offline_status": outcome.offline.summary if outcome.offline else None,
        "source_revision": outcome.revision,
        "identity_kind": outcome.identity_kind,
        "images_probed": False,
        "services_started": False,
        "executable_recipe": False,
        "phases": _PHASES,
        "replacements": _CHANGES,
        "unresolved": _RUNTIME,
        "unverified_runtime": outcome.offline.unverified_runtime
        if outcome.offline
        else (),
        "guidance": "docs/deployment/profile-rehearsal.md",
    }
    if json_output:
        print(json.dumps(report, sort_keys=True))
    else:
        print(outcome.status + ": preview only; no images probed or services started.")
        for code in outcome.codes:
            print("  " + code + ": " + action(code))
        for label, entries in (
            ("Planned phases", _PHASES),
            ("Replacements", _CHANGES),
            ("Unresolved", _RUNTIME),
        ):
            print(label + ":")
            for entry in entries:
                print("  " + entry)
        print("Guide: docs/deployment/profile-rehearsal.md")
    return {"PLAN_READY": 0, "FAIL": 1, "PENDING": 2}[outcome.status]


if __name__ == "__main__":
    raise SystemExit(main())
