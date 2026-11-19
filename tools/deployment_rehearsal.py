#!/usr/bin/env python3
"""Preview a disposable profile rehearsal. Never contacts a Docker daemon."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

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
except ModuleNotFoundError:
    import deployment_profile as checker

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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


def transform_recipe(
    recipe: dict[str, Any], snapshots: Mapping[str, str]
) -> dict[str, Any]:
    """Private symbolic recipe; never executable preview output."""
    transformed = copy.deepcopy(recipe)
    transformed["name"] = _SYMBOL
    transformed.pop("x-app-build", None)
    for category in ("volumes", "networks"):
        for name, definition in transformed[category].items():
            definition["name"] = _SYMBOL + "_" + name
            definition["labels"] = {_LABEL: _SYMBOL}
            if category == "networks":
                definition["internal"] = True
    transformed["secrets"] = {
        name: {
            "file": f"/__unresolved_rehearsal__/secrets/{name}",
            "name": _SYMBOL + "_" + name,
        }
        for name in sorted(_ROLES)
    }
    for name, service in transformed["services"].items():
        service.pop("build", None)
        service["image"] = (
            "${REHEARSAL_IMAGE_ID_"
            + str(
                sorted({item["image"] for item in recipe["services"].values()}).index(
                    service["image"]
                )
            )
            + "}"
        )
        service["labels"] = {**service.get("labels", {}), _LABEL: _SYMBOL}
        service.pop("ports", None)
        service["restart"] = "no"
        if name == "caddy":
            service["environment"]["SAPPHIRE_DOMAIN"] = ":80"
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                mount["source"] = (
                    "/__unresolved_rehearsal__/inputs/"
                    + snapshots[str(Path(mount["source"]).resolve())]
                )
                mount["read_only"] = True
                mount["bind"] = {**mount.get("bind", {}), "create_host_path": False}
    return transformed


def verify_transform(
    original: dict[str, Any], candidate: dict[str, Any], snapshots: Mapping[str, str]
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
        restore(restored, original, "name", _SYMBOL)
        restore(restored, original, "x-app-build", None)
        expected_secrets = {
            name: {
                "file": f"/__unresolved_rehearsal__/secrets/{name}",
                "name": _SYMBOL + "_" + name,
            }
            for name in _ROLES
        }
        restore(restored, original, "secrets", expected_secrets)
        for category in ("networks", "volumes"):
            for name, source in original[category].items():
                target = restored[category][name]
                restore(target, source, "name", _SYMBOL + "_" + name)
                restore(target, source, "labels", {_LABEL: _SYMBOL})
                if category == "networks":
                    restore(target, source, "internal", True)
        for name, source in original["services"].items():
            target = restored["services"][name]
            restore(target, source, "build", None)
            restore(
                target,
                source,
                "image",
                "${REHEARSAL_IMAGE_ID_"
                + str(
                    sorted(
                        {item["image"] for item in original["services"].values()}
                    ).index(source["image"])
                )
                + "}",
            )
            restore(target, source, "ports", None)
            restore(target, source, "restart", "no")
            restore(
                target, source, "labels", {**source.get("labels", {}), _LABEL: _SYMBOL}
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
                        "/__unresolved_rehearsal__/inputs/"
                        + snapshots[str(Path(before["source"]).resolve())]
                    )
                    restore(after, before, "source", expected_path)
                    restore(after, before, "read_only", True)
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


def preview(
    selection: ProfileSelection,
    *,
    identity_kind: IdentityKind = "release-target",
    root: Path = ROOT,
) -> Preview:
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
        return Preview(
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
    if checked.recipe is None:
        refuse("RECIPE_EVIDENCE_MISSING")
    revision = source_identity(selection, identity_kind, root)
    inputs = restrict_recipe(selection, checked.recipe, root)
    transformed = transform_recipe(checked.recipe, inputs)
    verify_transform(checked.recipe, transformed, inputs)
    verify_inputs(frozen, secrets)
    if source_identity(selection, identity_kind, root) != revision:
        refuse("SOURCE_DRIFT")
    return Preview(
        status="PLAN_READY",
        codes=(),
        offline=checked.evaluation,
        revision=revision,
        identity_kind=identity_kind,
    )


_ACTIONS = {
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


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        refuse("CLI_INPUT_INVALID")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_output = "json" in args or "--format=json" in args
    parser = Parser(description=__doc__)
    parser.add_argument("command", choices=("preview",))
    parser.add_argument("--profile-file", type=Path, required=True)
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--image-identity",
        choices=("release-target", "ci-development"),
        default="release-target",
    )
    identity_kind: IdentityKind = "release-target"
    try:
        options = parser.parse_args(args)
        identity_kind = options.image_identity
        for path in (options.profile_file, options.installation):
            guard_path(path)
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
        outcome = preview(selection, identity_kind=options.image_identity)
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
