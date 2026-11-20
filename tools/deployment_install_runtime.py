"""Fresh persistent installation and guarded retained-container lifecycle."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

try:
    from tools import deployment_install as preview
    from tools import deployment_rehearsal as common
    from tools.deployment_install_state import (
        LOCK,
        SERVICES,
        Delivery,
        InstallationRecord,
        InstallError,
        ResourceKind,
        check_fingerprints,
        installation_lock,
        load_recipe,
        load_record,
        read_deliveries,
        save_fingerprints,
        save_record,
        unchanged,
    )
except ModuleNotFoundError:
    import deployment_install as preview
    import deployment_rehearsal as common
    from deployment_install_state import (
        LOCK,
        SERVICES,
        Delivery,
        InstallationRecord,
        InstallError,
        ResourceKind,
        check_fingerprints,
        installation_lock,
        load_recipe,
        load_record,
        read_deliveries,
        save_fingerprints,
        save_record,
        unchanged,
    )

LABEL = "org.sapphire.installation"
PROJECT_LABEL = "com.docker.compose.project"
SOCKET = "unix:///var/run/docker.sock"
WORKERS = ("prefect-worker", "prefect-worker-ingest", "prefect-worker-backup")
STOP_ORDER = (*WORKERS, "api", "caddy", "init", "prefect-server", "postgres")
COMMANDS: dict[ResourceKind, list[str]] = {
    "container": ["ps", "-aq"],
    "network": ["network", "ls", "-q"],
    "volume": ["volume", "ls", "-q"],
}
PAUSE_CODES = {
    "pool_inventory_invalid",
    "pool_pause_not_confirmed",
    "deployment_inventory_invalid",
    "schedule_inventory_invalid",
    "schedule_pause_not_confirmed",
    "unexpected_flow_execution",
    "flow_inventory_overflow",
    "flow_history_overflow",
}


def runtime_environment(
    environment: Mapping[str, str], *, operation: str = "install"
) -> None:
    forbidden = {
        "DB_PASSWORD",
        "ACCESS_TOKEN_PEPPER",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    if operation == "stop":
        forbidden -= {"DB_PASSWORD", "ACCESS_TOKEN_PEPPER"}
    if any(environment.get(name) for name in forbidden):
        raise InstallError("ambient_runtime_override_refused")
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise InstallError("linux_amd64_required")


def host_preflight(
    driver: common.Runtime, scope: Literal["new", "retained"] = "new"
) -> str:
    info = json.loads(driver.run(["info", "--format", "{{json .}}"]))
    if (
        info.get("OSType") != "linux"
        or info.get("Architecture") not in {"x86_64", "amd64"}
        or int(info.get("ServerVersion", "0").split(".")[0]) < 28
        or any("rootless" in item for item in info.get("SecurityOptions", []))
        or info.get("Swarm", {}).get("LocalNodeState", "inactive") != "inactive"
    ):
        raise InstallError("docker_host_unsupported")
    daemon = info.get("ID", "")
    if not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", daemon):
        raise InstallError("daemon_identity_invalid")
    if scope == "new" and driver.run(["ps", "-q"]):
        raise InstallError("dedicated_idle_daemon_required")
    return daemon


def check_host_port(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))
    except OSError:
        raise InstallError("host_port_unavailable") from None


class Session:
    def __init__(self, driver: common.Runtime, record: InstallationRecord) -> None:
        self.driver, self.record = driver, record

    def guard(self) -> None:
        if self.driver.daemon() != self.record.daemon:
            raise InstallError("daemon_changed")

    def inspect(self, kind: ResourceKind, identifier: str) -> dict[str, Any]:
        self.guard()
        items = json.loads(self.driver.run([kind, "inspect", identifier]))
        if len(items) != 1:
            raise InstallError("resource_identity_invalid")
        item = items[0]
        labels = (
            item.get("Config", {}).get("Labels", {})
            if kind == "container"
            else item.get("Labels", {})
        )
        if (
            labels.get(LABEL) != self.record.installation_id
            or labels.get(PROJECT_LABEL) != self.record.project
            or item.get("Name", "").lstrip("/") not in self.record.names(kind)
        ):
            raise InstallError("foreign_resource_refused")
        return item

    def inventory(self) -> dict[ResourceKind, list[str]]:
        self.guard()
        inventory: dict[ResourceKind, list[str]] = {}
        for kind, command in COMMANDS.items():
            identifiers = self.driver.run(
                [
                    *command,
                    "--filter",
                    "label=" + PROJECT_LABEL + "=" + self.record.project,
                ]
            ).splitlines()
            for name in self.record.names(kind):
                identifiers.extend(
                    self.driver.run(
                        [*command, "--filter", "name=^" + name + "$"]
                    ).splitlines()
                )
            for known_kind, identifier in self.record.known:
                if known_kind == kind:
                    selector = (
                        "name=^" + identifier + "$"
                        if kind == "volume"
                        else "id=" + identifier
                    )
                    identifiers.extend(
                        self.driver.run([*command, "--filter", selector]).splitlines()
                    )
            unique = list(dict.fromkeys(identifiers))
            if len(unique) > len(self.record.names(kind)):
                raise InstallError("resource_inventory_overflow")
            for identifier in unique:
                self.inspect(kind, identifier)
            inventory[kind] = unique
        return inventory

    def checkpoint(self, phase: str) -> None:
        inventory = self.inventory()
        self.record = replace(
            self.record,
            phase=phase,
            containment=None if phase == "installed" else self.record.containment,
            known=tuple(
                dict.fromkeys(
                    (
                        *self.record.known,
                        *(
                            (kind, identifier)
                            for kind, identifiers in inventory.items()
                            for identifier in identifiers
                        ),
                    )
                )
            ),
        )
        save_record(self.driver.directory, self.record)

    def collision_check(self) -> None:
        self.guard()
        for kind, command in COMMANDS.items():
            if self.driver.run(
                [
                    *command,
                    "--filter",
                    "label=" + PROJECT_LABEL + "=" + self.record.project,
                ]
            ):
                raise InstallError("resource_collision")
            for name in self.record.names(kind):
                if self.driver.run([*command, "--filter", "name=^" + name + "$"]):
                    raise InstallError("resource_collision")

    def service(self, name: str) -> tuple[str, dict[str, Any]]:
        if name not in SERVICES:
            raise InstallError("service_refused")
        item = self.inspect("container", self.record.project + "-" + name + "-1")
        if item["Config"]["Labels"].get("com.docker.compose.service") != name:
            raise InstallError("service_identity_invalid")
        return item["Id"], item

    def mark_incomplete(self) -> None:
        self.record = replace(self.record, containment="incomplete")
        try:
            save_record(self.driver.directory, self.record)
        except Exception:
            return

    def stop(self) -> None:
        inventory = self.inventory()
        by_name = {
            self.inspect("container", identifier)["Name"].lstrip("/"): identifier
            for identifier in inventory["container"]
        }
        for service in STOP_ORDER:
            identifier = by_name.get(self.record.project + "-" + service + "-1")
            if identifier is not None:
                item = self.inspect("container", identifier)
                if item["State"].get("Running"):
                    seconds = 40 if service == "postgres" else 8
                    self.driver.run(
                        ["stop", "--time", str(seconds), identifier],
                        timeout=seconds + 5,
                    )
                    if self.inspect("container", identifier)["State"].get("Running"):
                        raise InstallError("stop_not_confirmed")
        remaining = self.inventory()["container"]
        if any(
            self.inspect("container", identifier)["State"].get("Running")
            for identifier in remaining
        ):
            raise InstallError("stop_not_confirmed")
        self.record = replace(self.record, containment="stopped_data_retained")
        self.checkpoint(
            "stopped" if self.record.phase in {"installed", "stopped"} else "failed"
        )


class Probes:
    def __init__(self, session: Session, directory: Path) -> None:
        self.session, self.directory = session, directory
        self.attempt = secrets.token_hex(8)
        self.names: list[str] = []
        self.pending: set[str] = set()

    def run(self, args: list[str], *, stdin: str | None = None) -> str:
        self.session.guard()
        driver = self.session.driver
        if args[0] != "run":
            if args[:2] != ["image", "inspect"]:
                raise InstallError("probe_operation_refused")
            return driver.run(args, stdin=stdin)
        name = (
            "sapphire-install-probe-"
            + self.session.record.installation_id
            + "-"
            + self.attempt
            + "-"
            + str(len(self.names))
        )
        self.names.append(name)
        self.pending.add(name)
        common.durable_json(
            self.directory / ("probes-" + self.attempt + ".json"),
            {
                "daemon": self.session.record.daemon,
                "installation_id": self.session.record.installation_id,
                "names": self.names,
            },
        )
        try:
            return driver.run(
                [
                    "run",
                    "--name",
                    name,
                    "--label",
                    LABEL + "=" + self.session.record.installation_id,
                    *args[1:],
                ],
                stdin=stdin,
            )
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        for name in tuple(self.pending):
            self.session.guard()
            identifiers = self.session.driver.run(
                ["ps", "-aq", "--filter", "name=^" + name + "$"]
            ).splitlines()
            for identifier in identifiers:
                self.session.guard()
                item = json.loads(
                    self.session.driver.run(["container", "inspect", identifier])
                )[0]
                if (
                    item.get("Name", "").lstrip("/") != name
                    or item.get("Config", {}).get("Labels", {}).get(LABEL)
                    != self.session.record.installation_id
                ):
                    raise InstallError("foreign_probe_refused")
                self.session.driver.run(["rm", "-f", identifier])
            self.pending.remove(name)


def resolve_images(
    session: Session,
    probes: Probes,
    recipe: dict[str, Any],
    environment: Mapping[str, str],
) -> dict[str, str]:
    images: dict[str, str] = {}
    variants: tuple[tuple[Literal["default", "aquacast"], str], ...] = (
        ("default", "api"),
        ("aquacast", "prefect-worker"),
    )
    for variant, service in variants:
        tag = recipe["services"][service]["image"]
        result = common.image_probe.probe_image(
            tag,
            environment["SAPPHIRE_RELEASE_VERSION"],
            environment["SAPPHIRE_SOURCE_REVISION"],
            variant=variant,
            runner=probes,
        )
        images[tag] = result.image_id
        inspect_image = json.loads(
            session.driver.run(["image", "inspect", result.image_id])
        )[0]
        if inspect_image.get("Config", {}).get("User", "") not in {
            "",
            "root",
            "0",
            "0:0",
        }:
            raise InstallError("image_consumer_user_invalid")
        observed = probes.run(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--user",
                "0",
                "--entrypoint",
                "/app/.venv/bin/python",
                result.image_id,
                "-c",
                "import pwd; p = pwd.getpwnam('app'); "
                "print('valid' if (p.pw_uid, p.pw_gid) == (1000, 1000) else 'invalid')",
            ]
        )
        if observed != "valid":
            raise InstallError("image_consumer_user_invalid")
    for name in ("postgres", "prefect-server", "caddy"):
        tag = recipe["services"][name]["image"]
        item = json.loads(session.driver.run(["image", "inspect", tag]))[0]
        if (
            "@sha256:" not in tag
            or item.get("Os") != "linux"
            or item.get("Architecture") != "amd64"
            or (
                name in {"postgres", "prefect-server"}
                and item.get("Config", {}).get("User", "")
                not in {"", "root", "0", "0:0"}
            )
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("Id", ""))
        ):
            raise InstallError("infrastructure_image_invalid")
        images[tag] = item["Id"]
    return images


def probe_deliveries(
    probes: Probes, image: str, deliveries: tuple[Delivery, ...]
) -> None:
    for user, selected in (("0", deliveries[:1]), ("1000:1000", deliveries[1:])):
        mounts: list[str] = []
        for delivery in selected:
            if any(character in str(delivery.path) for character in (",", "\n", "\r")):
                raise InstallError("credential_path_unsupported")
            mounts.extend(
                [
                    "--mount",
                    "type=bind,src="
                    + str(delivery.path)
                    + ",dst=/run/secrets/"
                    + delivery.name
                    + ",readonly",
                ]
            )
        script = (
            "from pathlib import Path\n"
            + "\n".join(
                "assert Path('/run/secrets/" + d.name + "').read_bytes()"
                for d in selected
            )
            + "\nprint('readable')\n"
        )
        result = probes.run(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--user",
                user,
                "--entrypoint",
                "/app/.venv/bin/python",
                *mounts,
                "-i",
                image,
                "-",
            ],
            stdin=script,
        )
        if result != "readable":
            raise InstallError("credential_readability_failed")
    unchanged(deliveries)


def materialize(
    selection: common.ProfileSelection,
    prepared: common.Prepared,
    layout: preview.InstallationLayout,
    images: Mapping[str, str],
    installation_id: str,
) -> dict[str, Any]:
    if prepared.recipe is None:
        raise InstallError("recipe_evidence_missing")
    result = preview.transform_recipe(
        prepared.recipe, dict(prepared.snapshots), selection, layout
    )
    preview.verify_transform(
        prepared.recipe, result, dict(prepared.snapshots), selection, layout
    )
    for name, service in result["services"].items():
        service["image"] = images[prepared.recipe["services"][name]["image"]]
        service["labels"][LABEL] = installation_id
    for category in ("networks", "volumes"):
        for resource in result[category].values():
            resource["labels"][LABEL] = installation_id
    return result


def compose(session: Session, executable: str) -> list[str]:
    return [
        executable,
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(session.driver.directory),
        "--project-name",
        session.record.project,
        "-f",
        str(session.driver.directory / "recipe.json"),
    ]


def verify_render(session: Session, executable: str, expected: dict[str, Any]) -> None:
    rendered = json.loads(
        session.driver.command(
            [
                *compose(session, executable),
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ]
        )
    )
    candidate = copy.deepcopy(rendered)
    for service in candidate["services"].values():
        for port in service.get("ports", []):
            if port.get("mode") == "ingress":
                port.pop("mode")
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                mount["source"] = mount["source"].replace("$", "$$")
                mount.setdefault("bind", {}).setdefault("create_host_path", False)
    for secret in candidate["secrets"].values():
        secret["file"] = secret["file"].replace("$", "$$")
    if candidate != expected:
        raise InstallError("runtime_render_changed")


def helper(session: Session, probes: Probes | None, image: str, action: str) -> None:
    if action not in {"prepare-pools", "pause-jobs", "verify-state", "workers", "api"}:
        raise InstallError("helper_action_refused")
    environment = [
        "-e",
        "SAPPHIRE_INSTALLATION_ID=" + session.record.installation_id,
        "-e",
        "PREFECT_API_URL=http://prefect-server:4200/api",
        "-e",
        "PREFECT_HOME=/tmp/prefect",
    ]
    module = ["-m", "sapphire_flow.cli.deployment_install", action]
    if probes is None or action == "api":
        identifier, _ = session.service("api")
        process = (
            ["/entrypoint.sh", "python"]
            if action == "api"
            else ["/app/.venv/bin/python"]
        )
        user = "0" if action == "api" else "1000:1000"
        output = session.driver.run(
            ["exec", "--user", user, *environment, identifier, *process, *module]
        )
    else:
        output = probes.run(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                session.record.project + "_backend",
                "--user",
                "1000:1000",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--entrypoint",
                "/app/.venv/bin/python",
                *environment,
                image,
                *module,
            ]
        )
    response = json.loads(output)
    if response != {"status": "ok", "stage": action}:
        code = response.get("code")
        if code in PAUSE_CODES | {"workers_not_ready", "probe_token_revocation_failed"}:
            raise InstallError(code)
        raise InstallError("helper_verification_failed")


def wait_healthy(
    session: Session, names: tuple[str, ...], sleep: Callable[[float], None]
) -> None:
    while True:
        states = [session.service(name)[1]["State"] for name in names]
        if any(s["Status"] in {"dead", "exited"} for s in states):
            raise InstallError("service_start_failed")
        if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
            return
        if session.driver.deadline - session.driver.clock() < 2:
            raise InstallError("service_start_timeout")
        sleep(1)


def host_access(session: Session) -> None:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for path, expected in (("/api/v1/health", 200), ("/api/v1/stations", 401)):
        try:
            remaining = session.driver.deadline - session.driver.clock()
            if remaining <= 0:
                raise InstallError("host_access_timeout")
            with opener.open(
                f"http://127.0.0.1:{session.record.http_port}" + path,
                timeout=min(10, remaining),
            ) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            raise InstallError("host_access_failed") from None
        if status != expected:
            raise InstallError("host_access_failed")


def inspect_contract(
    session: Session, recipe: dict[str, Any], deliveries: tuple[Delivery, ...]
) -> Literal["running", "stopped"]:
    inventory = session.inventory()
    current = {
        (kind, identifier)
        for kind, identifiers in inventory.items()
        for identifier in identifiers
    }
    if session.record.phase in {"installed", "stopped"} and current != set(
        session.record.known
    ):
        raise InstallError("retained_resources_changed")
    running: list[bool] = []
    image_configs: dict[str, dict[str, Any]] = {}
    secrets_by_name = {item.name: item.path for item in deliveries}
    for name in SERVICES:
        identifier, item = session.service(name)
        wanted = recipe["services"][name]
        config, host = item["Config"], item["HostConfig"]
        if (
            item.get("Image") != wanted["image"]
            or host.get("RestartPolicy", {}).get("Name") != wanted["restart"]
            or host.get("Privileged")
            or host.get("ReadonlyRootfs", False) != wanted.get("read_only", False)
        ):
            raise InstallError("container_configuration_changed")
        if wanted["image"] not in image_configs:
            image_configs[wanted["image"]] = json.loads(
                session.driver.run(["image", "inspect", wanted["image"]])
            )[0]["Config"]
        for key, actual_key in (("command", "Cmd"), ("entrypoint", "Entrypoint")):
            configured = wanted.get(key)
            expected = (
                image_configs[wanted["image"]].get(actual_key)
                if configured is None
                else [value.replace("$$", "$") for value in configured]
            )
            if config.get(actual_key) != expected:
                raise InstallError("container_configuration_changed")
        actual_env = dict(
            value.split("=", 1) for value in config.get("Env", []) if "=" in value
        )
        if any(
            actual_env.get(key) != str(value)
            for key, value in wanted.get("environment", {}).items()
        ):
            raise InstallError("container_environment_changed")
        if any(actual_env.get(key) for key in ("DB_PASSWORD", "ACCESS_TOKEN_PEPPER")):
            raise InstallError("ambient_runtime_override_refused")
        for key, actual_key in (("cap_add", "CapAdd"), ("cap_drop", "CapDrop")):
            if {v.removeprefix("CAP_") for v in host.get(actual_key, []) or ()} != {
                v.removeprefix("CAP_") for v in wanted.get(key, [])
            }:
                raise InstallError("container_privileges_changed")
        ports: dict[str, Any] = host.get("PortBindings") or {}
        expected_ports = (
            {
                "80/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": str(session.record.http_port)}
                ]
            }
            if name == "caddy"
            else {}
        )
        if ports != expected_ports or host.get("PublishAllPorts", False):
            raise InstallError("port_publication_changed")
        if item["State"].get("Running"):
            publications = {
                port: bindings
                for port, bindings in item.get("NetworkSettings", {})
                .get("Ports", {})
                .items()
                if bindings
            }
            if publications != expected_ports:
                raise InstallError("actual_publication_changed")
        if set(item.get("NetworkSettings", {}).get("Networks", {})) != {
            session.record.project + "_" + network for network in wanted["networks"]
        }:
            raise InstallError("container_networks_changed")
        actual_mounts = {
            mount["Destination"]: mount for mount in item.get("Mounts", [])
        }
        expected_targets: set[str] = set()
        for mount in wanted.get("volumes", []):
            target = mount["target"]
            expected_targets.add(target)
            actual = actual_mounts.get(target, {})
            if actual.get("Type") != mount["type"] or actual.get("RW") != (
                not mount.get("read_only", False)
            ):
                raise InstallError("container_mount_changed")
            if mount["type"] == "bind" and actual.get("Source") != mount[
                "source"
            ].replace("$$", "$"):
                raise InstallError("container_mount_changed")
            if (
                mount["type"] == "volume"
                and actual.get("Name") != session.record.project + "_" + mount["source"]
            ):
                raise InstallError("container_mount_changed")
        for grant in wanted.get("secrets", []):
            role = grant["source"]
            target = "/run/secrets/" + role
            expected_targets.add(target)
            actual = actual_mounts.get(target, {})
            if (
                actual.get("Type") != "bind"
                or actual.get("RW") is not False
                or actual.get("Source") != str(secrets_by_name[role])
            ):
                raise InstallError("credential_mount_invalid")
        expected_targets.update(
            path.split(":", 1)[0] for path in wanted.get("tmpfs", [])
        )
        if set(actual_mounts) - expected_targets:
            raise InstallError("unexpected_container_mount")
        if name == "init":
            if (
                item["State"].get("Running")
                or item["State"].get("ExitCode") != 0
                or (identifier, item["State"].get("FinishedAt"))
                != session.record.init_identity
            ):
                raise InstallError("init_state_changed")
        else:
            running.append(item["State"].get("Running") is True)
    if not any(running):
        return "stopped"
    if not all(running):
        raise InstallError("partial_running_state")
    return "running"


def verify_file_mounts(
    service: dict[str, Any], item: dict[str, Any], deliveries: tuple[Delivery, ...]
) -> None:
    expected = {
        mount["target"]: mount["source"].replace("$$", "$")
        for mount in service.get("volumes", [])
        if mount["type"] == "bind"
    }
    paths = {delivery.name: str(delivery.path) for delivery in deliveries}
    expected.update(
        {
            "/run/secrets/" + grant["source"]: paths[grant["source"]]
            for grant in service.get("secrets", [])
        }
    )
    mounts = {mount["Destination"]: mount for mount in item.get("Mounts", [])}
    for target, source in expected.items():
        mount = mounts.get(target, {})
        if (
            mount.get("Type") != "bind"
            or mount.get("RW") is not False
            or mount.get("Source") != source
        ):
            raise InstallError("readonly_delivery_mount_not_confirmed")


def verify_database(session: Session, recipe: dict[str, Any], tenant: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(
        Config(str(preview.ROOT / "alembic.ini"))
    ).get_current_head()
    if head is None or not re.fullmatch(r"[A-Za-z0-9_]+", head):
        raise InstallError("migration_head_invalid")
    identifier, _ = session.service("postgres")
    sql = """SELECT CASE WHEN
      (SELECT count(*) FROM alembic_version WHERE version_num = :'head') = 1
      AND (SELECT count(*) FROM alembic_version) = 1
      AND (SELECT count(*) FROM tenants WHERE code = :'tenant') = 1
      AND (SELECT count(*) FROM pg_roles WHERE rolname IN
        ('sapphire_api','sapphire_worker','sapphire_backup') AND rolcanlogin
        AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls) = 3
      AND has_table_privilege('sapphire_api','stations','SELECT')
      AND NOT has_table_privilege('sapphire_api','stations','INSERT')
      AND has_table_privilege('sapphire_backup','stations','SELECT')
      THEN 'ok' ELSE 'failed' END;
"""
    output = session.driver.run(
        [
            "exec",
            "-i",
            "--user",
            "postgres",
            identifier,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "head=" + head,
            "-v",
            "tenant=" + tenant,
            "-U",
            recipe["services"]["postgres"]["environment"]["POSTGRES_USER"],
            "-d",
            "sapphire",
            "-At",
        ],
        stdin=sql,
    )
    if output != "ok":
        raise InstallError("database_bootstrap_invalid")


def verify_role_auth(
    probes: Probes, image: str, deliveries: tuple[Delivery, ...]
) -> None:
    mounts: list[str] = []
    for delivery in deliveries[1:4]:
        mounts.extend(
            [
                "--mount",
                "type=bind,src="
                + str(delivery.path)
                + ",dst=/run/secrets/"
                + delivery.name
                + ",readonly",
            ]
        )
    script = """from pathlib import Path
import psycopg
for role in ('sapphire_api', 'sapphire_worker', 'sapphire_backup'):
    path = Path('/run/secrets/' + role + '_db_password')
    password = path.read_text().removesuffix('\\n')
    with psycopg.connect(host='postgres', dbname='sapphire', user=role,
                        password=password, connect_timeout=10) as conn:
        assert conn.execute('SELECT current_user').fetchone()[0] == role
print('authenticated')
"""
    output = probes.run(
        [
            "run",
            "--rm",
            "--pull",
            "never",
            "--read-only",
            "--network",
            probes.session.record.project + "_backend",
            "--user",
            "1000:1000",
            "--cap-drop",
            "ALL",
            "--entrypoint",
            "/app/.venv/bin/python",
            *mounts,
            "-i",
            image,
            "-",
        ],
        stdin=script,
    )
    if output != "authenticated":
        raise InstallError("database_authentication_failed")


def fresh_start(
    session: Session,
    probes: Probes,
    executable: str,
    recipe: dict[str, Any],
    tenant: str,
    deliveries: tuple[Delivery, ...],
    sleep: Callable[[float], None],
) -> None:
    def stage(name: str) -> None:
        session.record = replace(session.record, stage=name)
        save_record(session.driver.directory, session.record)

    def up(*names: str) -> None:
        stage("create_" + names[0])
        session.inventory()
        session.guard()
        try:
            session.driver.command(
                [
                    *compose(session, executable),
                    "up",
                    "--no-start",
                    "--no-deps",
                    "--no-build",
                    "--pull",
                    "never",
                    *names,
                ]
            )
        finally:
            session.checkpoint("installing")
        for name in names:
            identifier, item = session.service(name)
            verify_file_mounts(recipe["services"][name], item, deliveries)
            session.driver.run(["start", identifier])

    up("postgres")
    wait_healthy(session, ("postgres",), sleep)
    up("prefect-server")
    wait_healthy(session, ("prefect-server",), sleep)
    image = recipe["services"]["api"]["image"]
    stage("prepare_pools")
    helper(session, probes, image, "prepare-pools")
    up("init")
    identifier, _ = session.service("init")
    if (
        session.driver.run(
            ["wait", identifier],
            timeout=session.driver.deadline - session.driver.clock(),
        )
        != "0"
    ):
        raise InstallError("init_failed")
    item = session.inspect("container", identifier)
    session.record = replace(
        session.record, init_identity=(identifier, item["State"]["FinishedAt"])
    )
    session.checkpoint("installing")
    stage("database_checks")
    verify_database(session, recipe, tenant)
    verify_role_auth(probes, image, deliveries)
    stage("pause_jobs")
    helper(session, probes, image, "pause-jobs")
    up("api", "caddy", *WORKERS)
    wait_healthy(session, ("api", "caddy"), sleep)
    stage("service_checks")
    await_workers(session, image, sleep)
    inspect_contract(session, recipe, deliveries)
    host_access(session)
    stage("api_probe")
    helper(session, None, image, "api")
    helper(session, None, image, "verify-state")
    unchanged(deliveries)
    session.checkpoint("installed")


def await_workers(session: Session, image: str, sleep: Callable[[float], None]) -> None:
    while True:
        try:
            for name in ("postgres", "prefect-server", "api", "caddy"):
                if (
                    session.service(name)[1]["State"].get("Health", {}).get("Status")
                    != "healthy"
                ):
                    raise InstallError("service_unhealthy")
            helper(session, None, image, "workers")
            return
        except InstallError as exc:
            if (
                str(exc) != "workers_not_ready"
                or session.driver.deadline - session.driver.clock() < 2
            ):
                raise
            sleep(1)


def freeze(
    session: Session,
    prepared: common.Prepared,
    recipe: dict[str, Any],
    deliveries: tuple[Delivery, ...],
    random_bytes: Callable[[int], bytes],
) -> None:
    directory = session.driver.directory
    (directory / "inputs").mkdir(mode=0o700)
    snapshots = dict(prepared.snapshots)
    inputs: list[tuple[str, str, int]] = []
    for source in prepared.frozen:
        alias = snapshots.get(str(source.path.resolve()))
        if alias is not None:
            target = directory / "inputs" / alias
            with target.open("xb") as stream:
                stream.write(source.content)
                stream.flush()
                os.fsync(stream.fileno())
            mode = 0o444 | (source.mode & 0o111)
            target.chmod(mode)
            inputs.append((alias, hashlib.sha256(source.content).hexdigest(), mode))
    common.durable_json(directory / "recipe.json", recipe)
    save_fingerprints(directory, deliveries, random_bytes)
    session.record = replace(
        session.record,
        recipe_digest=hashlib.sha256(
            json.dumps(recipe, sort_keys=True).encode()
        ).hexdigest(),
        inputs=tuple(inputs),
    )
    save_record(directory, session.record)


def report(
    status: str,
    code: str | None = None,
    *,
    containment: str | None = None,
    http_port: int | None = None,
    token_probe: Literal["revoked", "not_run"] = "not_run",
    stage: str | None = None,
    failed_operation: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scope": "persistent_installation",
        "status": status,
        "code": code,
        "stage": stage,
        "failed_operation": failed_operation,
        "containment": containment,
        "http_binding": f"127.0.0.1:{http_port}:80/tcp" if http_port else None,
        "host_responses": {"health": 200, "unauthenticated_stations": 401}
        if http_port
        else None,
        "token_probe": token_probe,
        "checks": (
            "ownership",
            "frozen_configuration",
            "credential_values",
            "service_health",
            "paused_pools_jobs",
            "no_flow_execution",
        )
        if status in {"INSTALLED_PAUSED", "VERIFIED_PAUSED"}
        else (),
        "forecasting_enabled": False
        if status in {"INSTALLED_PAUSED", "VERIFIED_PAUSED", "STOPPED"}
        else None,
        "unverified": (
            "scientific_readiness",
            "station_model_onboarding",
            "backup_restore",
            "public_access",
            "host_reboot",
        ),
        "guidance": "docs/deployment/profile-installation.md",
    }


def failure_code(exc: BaseException) -> str:
    if isinstance(exc, (InstallError, common.RehearsalRuntimeError)) and re.fullmatch(
        r"[a-z_]+", str(exc)
    ):
        return str(exc)
    if isinstance(exc, common.ProfileError):
        return exc.code
    if isinstance(exc, common.image_probe.ImageIdentityProbeError):
        return "image_identity_invalid"
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    return "installation_failed"


def install(
    selection: common.ProfileSelection,
    layout: preview.InstallationLayout,
    *,
    identity_kind: common.IdentityKind = "release-target",
    executor: common.Executor = common.execute,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    lock_path: Path = LOCK,
) -> tuple[int, dict[str, Any]]:
    runtime_environment(os.environ)
    with installation_lock(lock_path):
        prepared = preview.prepare(selection, layout, identity_kind=identity_kind)
        if (
            prepared.preview.status != "PLAN_READY"
            or prepared.recipe is None
            or selection.installation is None
        ):
            return 2, report("REFUSED", "preview_prerequisites_unmet")
        directory = layout.state_dir.resolve()
        parent = directory.parent.stat()
        if parent.st_uid not in {0, os.getuid()} or parent.st_mode & 0o022:
            raise InstallError("state_parent_unsafe")
        deliveries = read_deliveries(dict(selection.installation.secret_bindings))
        docker = shutil.which("docker")
        if docker is None:
            raise InstallError("docker_unavailable")
        executable = common.compose_executable(selection)
        temporary = Path(tempfile.mkdtemp(prefix="sapphire-install-preflight-"))
        temporary.chmod(0o700)
        (temporary / "docker-config").mkdir(mode=0o700)
        driver = common.Runtime(
            directory=temporary,
            deadline=clock() + 900,
            clock=clock,
            executor=executor,
            docker=docker,
        )
        session: Session | None = None
        probes: Probes | None = None
        created = False
        result: tuple[int, dict[str, Any]] = (2, report("REFUSED"))
        try:
            daemon = host_preflight(driver)
            session = Session(
                driver,
                InstallationRecord(
                    installation_id=random_bytes(16).hex(),
                    project=selection.installation.project_name,
                    daemon=daemon,
                    http_port=layout.http_port,
                    revision=prepared.preview.revision or "",
                    identity_kind=identity_kind,
                    state_directory=str(directory),
                ),
            )
            probes = Probes(session, temporary)
            session.collision_check()
            check_host_port(layout.http_port)
            images = resolve_images(
                session,
                probes,
                prepared.recipe,
                dict(selection.installation.environment),
            )
            probe_deliveries(
                probes, images[prepared.recipe["services"]["api"]["image"]], deliveries
            )
            common.verify_inputs(prepared.frozen, tuple(d.path for d in deliveries))
            if (
                common.source_identity(selection, identity_kind, preview.ROOT)
                != prepared.preview.revision
            ):
                raise InstallError("source_drift")
            unchanged(deliveries)
            session.collision_check()
            directory.mkdir(mode=0o700)
            created = True
            driver.directory = directory
            (directory / "docker-config").mkdir(mode=0o700)
            save_record(directory, session.record)
            recipe = materialize(
                selection, prepared, layout, images, session.record.installation_id
            )
            freeze(session, prepared, recipe, deliveries, random_bytes)
            verify_render(session, executable, recipe)
            raw = common.load_merged_toml(
                directory
                / "inputs"
                / dict(prepared.snapshots)[
                    str(selection.profile.base_config.resolve())
                ],
                [
                    directory / "inputs" / dict(prepared.snapshots)[str(p.resolve())]
                    for p in selection.profile.overlays
                ],
                environment={},
            )
            tenant = next(
                iter(common.parse_deployment_identity_config(raw).writable_tenants)
            )
            fresh_start(session, probes, executable, recipe, tenant, deliveries, sleep)
            result = (
                0,
                report(
                    "INSTALLED_PAUSED",
                    http_port=session.record.http_port,
                    token_probe="revoked",
                ),
            )
        except BaseException as exc:
            code = failure_code(exc)
            failure_operation = driver.failed_operation
            if created and session is not None:
                driver.deadline = clock() + 120
                try:
                    session.stop()
                    session.record = replace(session.record, phase="failed")
                    save_record(directory, session.record)
                except Exception:
                    session.mark_incomplete()
                    result = 1, report("FAILED", code, containment="incomplete")
                else:
                    result = (
                        1,
                        report("FAILED", code, containment="stopped_data_retained"),
                    )
            else:
                driver.deadline = clock() + 120
                result = 2, report("REFUSED", code)
            result[1]["stage"] = session.record.stage if session else "preflight"
            result[1]["failed_operation"] = failure_operation
        finally:
            try:
                if probes is not None:
                    probes.cleanup()
            except Exception:
                if session is not None and session.driver.directory == directory:
                    session.mark_incomplete()
                result = (
                    1,
                    report(
                        "FAILED", "probe_cleanup_incomplete", containment="incomplete"
                    ),
                )
            else:
                try:
                    shutil.rmtree(temporary)
                except OSError:
                    result[1]["temporary_state_cleanup"] = "incomplete"
        return result


def lifecycle(
    command: Literal["verify", "stop", "start"],
    directory: Path,
    *,
    executor: common.Executor = common.execute,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    lock_path: Path = LOCK,
) -> tuple[int, dict[str, Any]]:
    runtime_environment(os.environ, operation=command)
    if not directory.is_absolute():
        raise InstallError("state_path_invalid")
    with installation_lock(lock_path):
        record = load_record(directory)
        docker = shutil.which("docker")
        if docker is None:
            raise InstallError("docker_unavailable")
        session = Session(
            common.Runtime(
                directory=directory,
                deadline=clock() + 900,
                clock=clock,
                executor=executor,
                docker=docker,
            ),
            record,
        )
        session.guard()
        if command == "stop":
            session.driver.deadline = clock() + 120
            session.inventory()
            try:
                session.stop()
            except BaseException as exc:
                session.mark_incomplete()
                return 1, report(
                    "FAILED",
                    failure_code(exc),
                    containment="incomplete",
                    stage="stop",
                    failed_operation=session.driver.failed_operation,
                )
            return 0, report("STOPPED")
        deliveries = check_fingerprints(directory)
        host_preflight(session.driver, "retained")
        recipe = load_recipe(directory, record)
        if record.phase not in {"installed", "stopped"}:
            raise InstallError("partial_installation_refused")
        state = inspect_contract(session, recipe, deliveries)
        image = recipe["services"]["api"]["image"]
        if command == "verify":
            if state == "stopped":
                return 0, report("STOPPED", "live_checks_not_run")
            for name in ("postgres", "prefect-server", "api", "caddy"):
                if (
                    session.service(name)[1]["State"].get("Health", {}).get("Status")
                    != "healthy"
                ):
                    raise InstallError("service_unhealthy")
            helper(session, None, image, "workers")
            host_access(session)
            unchanged(deliveries)
            return 0, report("VERIFIED_PAUSED", http_port=record.http_port)
        if state != "stopped":
            raise InstallError("already_running_refused")
        probes = Probes(session, directory)
        result: tuple[int, dict[str, Any]] = (1, report("FAILED", "restart_failed"))
        try:
            session.record = replace(session.record, stage="restart")
            save_record(directory, session.record)
            probe_deliveries(probes, image, deliveries)
            for name in ("postgres", "prefect-server"):
                identifier, _ = session.service(name)
                session.driver.run(["start", identifier])
                wait_healthy(session, (name,), sleep)
            helper(session, probes, image, "verify-state")
            for name in ("api", "caddy", *WORKERS):
                identifier, _ = session.service(name)
                session.driver.run(["start", identifier])
            wait_healthy(session, ("api", "caddy"), sleep)
            await_workers(session, image, sleep)
            inspect_contract(session, recipe, deliveries)
            host_access(session)
            unchanged(deliveries)
            session.checkpoint("installed")
            result = 0, report("INSTALLED_PAUSED", http_port=record.http_port)
        except BaseException as exc:
            code = failure_code(exc)
            failure_operation = session.driver.failed_operation
            session.driver.deadline = clock() + 120
            try:
                session.stop()
            except Exception:
                session.mark_incomplete()
                result = 1, report("FAILED", code, containment="incomplete")
            else:
                result = (
                    2 if code in PAUSE_CODES else 1,
                    report(
                        "REFUSED" if code in PAUSE_CODES else "FAILED",
                        code,
                        containment="stopped_data_retained",
                    ),
                )
            result[1]["stage"] = "restart"
            result[1]["failed_operation"] = failure_operation
        finally:
            try:
                probes.cleanup()
            except Exception:
                if session.driver.directory == directory:
                    session.mark_incomplete()
                result = (
                    1,
                    report(
                        "FAILED", "probe_cleanup_incomplete", containment="incomplete"
                    ),
                )
        return result


def dispatch(
    command: Literal["install", "verify", "stop", "start"],
    directory: Path,
    *,
    profile_file: Path | None,
    installation_file: Path | None,
    http_port: int,
    identity_kind: common.IdentityKind,
) -> tuple[int, dict[str, Any]]:
    try:
        if command == "install":
            if profile_file is None or installation_file is None:
                raise InstallError("explicit_selection_required")
            selectors = common.capture((profile_file, installation_file), ())
            selection = common.select_deployment_profile(
                profile_file=profile_file,
                installation_file=installation_file,
                ambient_environment=os.environ,
            )
            common.verify_inputs(
                selectors,
                tuple(path for _, path in selection.installation.secret_bindings)
                if selection.installation
                else (),
            )
            return install(
                selection,
                preview.InstallationLayout(state_dir=directory, http_port=http_port),
                identity_kind=identity_kind,
            )
        if (
            command not in {"verify", "stop", "start"}
            or profile_file is not None
            or installation_file is not None
            or http_port != 8080
            or identity_kind != "release-target"
        ):
            raise InstallError("lifecycle_arguments_invalid")
        return lifecycle(command, directory)
    except InstallError as exc:
        failed = str(exc) in {
            "host_access_failed",
            "host_access_timeout",
            "service_unhealthy",
            "workers_not_ready",
        }
        return (1 if failed else 2), report(
            "FAILED" if failed else "REFUSED", str(exc), stage=command
        )
    except common.RehearsalRuntimeError as exc:
        return 1, report(
            "FAILED",
            failure_code(exc),
            stage=command,
            containment="incomplete" if command == "stop" else None,
        )
    except common.ProfileError as exc:
        return 2, report("REFUSED", exc.code)
    except OSError:
        return 2, report("REFUSED", "runtime_prerequisite_unavailable")
    except Exception:
        return 1, report("FAILED", "runtime_operation_failed")
