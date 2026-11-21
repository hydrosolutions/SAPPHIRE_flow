#!/usr/bin/env python3
"""Preview or manage a new persistent installation in its paused phase."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from sapphire_flow.config.deployment_profile import (
    ProfileError,
    ProfileSelection,
    select_deployment_profile,
)
from sapphire_flow.exceptions import ConfigurationError

try:
    from tools import deployment_rehearsal as rehearsal
except ModuleNotFoundError:
    import deployment_rehearsal as rehearsal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
LABEL = "org.sapphire.installation"
SYMBOL = "unresolved-installation"
PHASES = (
    "recheck source, deliveries, prepared images and dedicated Docker host",
    "record ownership and freeze nonsecret configuration in private state",
    "start fresh database and Prefect; verify paused pools before normal init",
    "pause schedules; start services and verify loopback access without execution",
    "retain services, private state and data for separate onboarding",
)
CHANGES = (
    "retain selected project and standard named-volume sharing; add ownership labels",
    "pin prepared immutable image IDs; remove builds and build-only secrets",
    "retain five administrator-delivered credential files and their existing grants",
    "freeze approved nonsecret binds read-only without implicit path creation",
    "publish only Caddy HTTP on the selected IPv4 loopback port",
    "retain standard networks, service privileges and restart policies",
)
UNVERIFIED = (
    "dedicated Linux amd64 host and local rootful Docker Engine >=28",
    "resource and port availability; state-parent ownership and permissions",
    "actual image identity and upstream release admission",
    "credential values, reader permissions, read-only mounts and authentication",
    "paused jobs/pools, service health, host access and restart persistence",
    "station/model state, scientific readiness, providers and backup/restore",
)


def refuse(code: str) -> NoReturn:
    raise ProfileError(code, "See docs/deployment/profile-installation.md.")


@dataclass(frozen=True, kw_only=True, slots=True)
class InstallationLayout:
    state_dir: Path
    http_port: int = 8080

    def __post_init__(self) -> None:
        if (
            not self.state_dir.is_absolute()
            or any(ord(char) < 32 for char in str(self.state_dir))
            or self.state_dir == Path("/")
        ):
            refuse("STATE_PATH_INVALID")
        if not 1 <= self.http_port <= 65535:
            refuse("HTTP_PORT_INVALID")


def check_layout(layout: InstallationLayout, root: Path) -> None:
    directory = layout.state_dir.resolve()
    if directory.is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        directory
    ):
        refuse("STATE_PATH_INVALID")
    if layout.state_dir.is_symlink() or directory.exists():
        refuse("STATE_PATH_OCCUPIED")


def check_route(selection: ProfileSelection) -> None:
    if selection.installation is None:
        refuse("INSTALLATION_REQUIRED")
    environment = dict(selection.installation.environment)
    if (
        environment.get("SAPPHIRE_DOMAIN") != ":80"
        or environment.get("SAPPHIRE_CORS_ORIGINS") != ""
    ):
        refuse("LOCAL_ROUTE_REQUIRED")


def transform_recipe(
    recipe: dict[str, Any],
    snapshots: Mapping[str, str],
    selection: ProfileSelection,
    layout: InstallationLayout,
) -> dict[str, Any]:
    """Private symbolic recipe; never emitted or executed by preview."""
    installation = selection.installation
    if installation is None:
        refuse("INSTALLATION_REQUIRED")
    transformed = copy.deepcopy(recipe)
    transformed.pop("x-app-build", None)
    for category in ("volumes", "networks"):
        for resource in transformed[category].values():
            resource["labels"] = {LABEL: SYMBOL}
    transformed["secrets"] = {
        name: {
            "file": str(path).replace("$", "$$"),
            "name": installation.project_name + "_" + name,
        }
        for name, path in installation.secret_bindings
    }
    images = sorted({service["image"] for service in recipe["services"].values()})
    for name, service in transformed["services"].items():
        service.pop("build", None)
        service["image"] = (
            "${INSTALLATION_IMAGE_ID_" + str(images.index(service["image"])) + "}"
        )
        service["labels"] = {**service.get("labels", {}), LABEL: SYMBOL}
        service.pop("ports", None)
        if name == "caddy":
            service["ports"] = [
                {
                    "target": 80,
                    "published": str(layout.http_port),
                    "host_ip": "127.0.0.1",
                    "protocol": "tcp",
                }
            ]
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                mount["source"] = str(
                    layout.state_dir.resolve()
                    / "inputs"
                    / snapshots[str(Path(mount["source"]).resolve())]
                ).replace("$", "$$")
                mount["read_only"] = True
                mount["bind"] = {**mount.get("bind", {}), "create_host_path": False}
    return transformed


def verify_transform(
    original: dict[str, Any],
    candidate: dict[str, Any],
    snapshots: Mapping[str, str],
    selection: ProfileSelection,
    layout: InstallationLayout,
) -> None:
    restored = copy.deepcopy(candidate)
    installation = selection.installation
    if installation is None:
        refuse("INSTALLATION_REQUIRED")

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
        restore(restored, original, "x-app-build", None)
        restore(
            restored,
            original,
            "secrets",
            {
                name: {
                    "file": str(path).replace("$", "$$"),
                    "name": installation.project_name + "_" + name,
                }
                for name, path in installation.secret_bindings
            },
        )
        for category in ("volumes", "networks"):
            for name, source in original[category].items():
                restore(restored[category][name], source, "labels", {LABEL: SYMBOL})
        images = sorted({service["image"] for service in original["services"].values()})
        for name, source in original["services"].items():
            target = restored["services"][name]
            restore(target, source, "build", None)
            restore(
                target,
                source,
                "image",
                "${INSTALLATION_IMAGE_ID_" + str(images.index(source["image"])) + "}",
            )
            restore(
                target, source, "labels", {**source.get("labels", {}), LABEL: SYMBOL}
            )
            restore(
                target,
                source,
                "ports",
                [
                    {
                        "target": 80,
                        "published": str(layout.http_port),
                        "host_ip": "127.0.0.1",
                        "protocol": "tcp",
                    }
                ]
                if name == "caddy"
                else None,
            )
            for before, after in zip(
                source.get("volumes", []), target.get("volumes", []), strict=True
            ):
                if before["type"] == "bind":
                    restore(
                        after,
                        before,
                        "source",
                        str(
                            layout.state_dir.resolve()
                            / "inputs"
                            / snapshots[str(Path(before["source"]).resolve())]
                        ).replace("$", "$$"),
                    )
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


def prepare(
    selection: ProfileSelection,
    layout: InstallationLayout,
    *,
    identity_kind: rehearsal.IdentityKind = "release-target",
    root: Path = ROOT,
) -> rehearsal.Prepared:
    check_layout(layout, root)
    check_route(selection)
    prepared = rehearsal.prepare(selection, identity_kind=identity_kind, root=root)
    if prepared.recipe is not None:
        snapshots = dict(prepared.snapshots)
        candidate = transform_recipe(prepared.recipe, snapshots, selection, layout)
        verify_transform(prepared.recipe, candidate, snapshots, selection, layout)
    return prepared


def action(code: str) -> str:
    return {
        "LOCAL_ROUTE_REQUIRED": "Set installation SAPPHIRE_DOMAIN to :80 and "
        "SAPPHIRE_CORS_ORIGINS to an empty string.",
        "STATE_PATH_INVALID": "Choose an absolute private state directory outside "
        "the checkout under a stable parent.",
        "STATE_PATH_OCCUPIED": "Choose an unused state path; existing or partial "
        "installations cannot be adopted.",
        "HTTP_PORT_INVALID": "Choose a TCP port between 1 and 65535; host "
        "availability is checked at installation.",
        "CLI_INPUT_INVALID": "Use preview with --profile-file, --installation and "
        "--state-dir; lifecycle commands select only --state-dir.",
        "SOURCE_DIRTY": "Use a clean source checkout; preserve other people's work.",
        "SOURCE_IDENTITY_MISMATCH": "Align the source checkout with the reviewed "
        "release inputs; do not invent an identity to obtain a pass.",
        "PROTECTED_INPUT": "Separate configuration files from .env and credential "
        "deliveries, including aliases. Do not share their contents.",
        "INPUT_INVALID": "Check that selected files are readable and contain valid "
        "configuration; share only this safe report with deployment support.",
        "INSTALLATION_REQUIRED": "Supply the external installation file.",
        "TRANSFORM_INVALID": "The proposed recipe failed its invariant check; "
        "send this safe report to deployment support.",
        "unexpected_failure": "Send this safe report to deployment support; "
        "a tool failure prevented preview completion.",
    }.get(
        code,
        "Check the diagnostic in the profile/rehearsal reference or ask deployment "
        "support. Preview does not launch services.",
    )


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        refuse("CLI_INPUT_INVALID")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    json_output = "json" in args or "--format=json" in args
    parser = Parser(description=__doc__)
    parser.add_argument(
        "command", choices=("preview", "install", "verify", "stop", "start")
    )
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--image-identity",
        choices=("release-target", "ci-development"),
        default="release-target",
    )
    outcome: rehearsal.Preview | None = None
    layout: InstallationLayout | None = None
    identity_kind = "release-target"
    status = "REFUSED"
    codes: tuple[str, ...] = ()
    exit_code = 2
    proposed: dict[str, int] | None = None
    try:
        options = parser.parse_args(args)
        if options.command != "preview":
            try:
                from tools.deployment_install_runtime import dispatch
            except ModuleNotFoundError:
                from deployment_install_runtime import dispatch
            with rehearsal.runtime_signals():
                code, runtime_report = dispatch(
                    options.command,
                    options.state_dir,
                    profile_file=options.profile_file,
                    installation_file=options.installation,
                    http_port=options.http_port,
                    identity_kind=options.image_identity,
                )
            if json_output:
                sys.stdout.write(json.dumps(runtime_report, sort_keys=True) + "\n")
            else:
                sys.stdout.write(
                    f"{runtime_report['status']}: "
                    f"{runtime_report.get('code') or 'completed'}\n"
                    "Reference: docs/deployment/profile-installation.md\n"
                )
                for field in (
                    "stage",
                    "failed_operation",
                    "containment",
                    "http_binding",
                    "forecasting_enabled",
                    "unverified",
                    "temporary_state_cleanup",
                ):
                    if runtime_report.get(field) is not None:
                        sys.stdout.write(f"{field}: {runtime_report[field]}\n")
            return code
        if options.profile_file is None or options.installation is None:
            refuse("CLI_INPUT_INVALID")
        identity_kind = options.image_identity
        layout = InstallationLayout(
            state_dir=options.state_dir, http_port=options.http_port
        )
        selectors = rehearsal.capture((options.profile_file, options.installation), ())
        selection = select_deployment_profile(
            profile_file=options.profile_file,
            installation_file=options.installation,
            ambient_environment=os.environ,
        )
        rehearsal.verify_inputs(
            selectors,
            tuple(path for _, path in selection.installation.secret_bindings)
            if selection.installation
            else (),
        )
        prepared = prepare(selection, layout, identity_kind=options.image_identity)
        outcome = prepared.preview
        codes = outcome.codes
        if outcome.status == "PLAN_READY":
            status, exit_code = "PLAN_READY", 0
            if prepared.recipe is not None:
                services = prepared.recipe["services"]
                proposed = {
                    "services": len(services),
                    "image_references": len({s["image"] for s in services.values()}),
                    "named_volumes": len(prepared.recipe["volumes"]),
                    "configuration_mounts": sum(
                        mount["type"] == "bind"
                        for service in services.values()
                        for mount in service.get("volumes", [])
                    ),
                    "runtime_credentials": len(selection.installation.secret_bindings)
                    if selection.installation
                    else 0,
                    "published_ports": 1,
                }
    except ProfileError as exc:
        codes = (exc.code,)
        if exc.code == "TRANSFORM_INVALID":
            status, exit_code = "FAIL", 1
    except (OSError, ConfigurationError):
        codes = ("INPUT_INVALID",)
    except Exception:
        status, codes, exit_code = "FAIL", ("unexpected_failure",), 1
    report = {
        "schema_version": 1,
        "scope": "persistent_installation_preview",
        "status": status,
        "codes": codes,
        "actions": tuple(action(code) for code in codes),
        "source_revision": outcome.revision if outcome else None,
        "identity_kind": identity_kind,
        "offline": asdict(outcome.offline) if outcome and outcome.offline else None,
        "state_directory": "private-installation-state",
        "http_binding": f"127.0.0.1:{layout.http_port}:80/tcp" if layout else None,
        "proposed": proposed if status == "PLAN_READY" else None,
        "daemon_contacted": False,
        "credentials_read": False,
        "images_probed": False,
        "services_started": False,
        "executable_recipe": False,
        "launching": "separate_install_command",
        "phases": PHASES,
        "changes": CHANGES,
        "unverified": UNVERIFIED,
        "guidance": "docs/deployment/profile-installation.md",
    }
    if json_output:
        sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            f"{status}: persistent installation preview only; "
            "nothing started; installation requires an explicit install command.\n"
        )
        if layout:
            sys.stdout.write(
                f"Proposed local state directory: {str(layout.state_dir)!r}\n"
                f"Proposed HTTP binding: {report['http_binding']}\n"
            )
        for code in codes:
            sys.stdout.write(f"{code}: {action(code)}\n")
        for change in CHANGES:
            sys.stdout.write(f"Change: {change}\n")
        if proposed is not None and status == "PLAN_READY":
            sys.stdout.write("Proposed counts: " + json.dumps(proposed) + "\n")
        if outcome and outcome.offline:
            for diagnostic in (
                *outcome.offline.diagnostics,
                *outcome.offline.pending_checks,
            ):
                sys.stdout.write(
                    f"{diagnostic.code} [owner={diagnostic.owner}]: "
                    f"{diagnostic.cause} {diagnostic.next_action}\n"
                )
            for setting in outcome.offline.provenance:
                value = (
                    setting.value
                    if setting.visibility == "value"
                    else f"<{setting.visibility}>"
                )
                sys.stdout.write(
                    f"{setting.field} = {value} ({setting.source}; "
                    f"{setting.operation}; default={setting.default_status})\n"
                )
        sys.stdout.write(
            "Unverified: "
            + "; ".join(UNVERIFIED)
            + "\nReference: docs/deployment/profile-installation.md\n"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
