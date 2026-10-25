from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum

_spec = importlib.util.spec_from_file_location(
    "exporter_staging_release", Path(__file__).with_name("staging_release.py")
)
assert _spec is not None and _spec.loader is not None
stage = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = stage
_spec.loader.exec_module(stage)
transfer, release = stage.transfer, stage.release

ENGINE_VERSION = "28.0.4"
SOCKET = "unix:///var/run/docker.sock"
BUILDER = "private-verification"
BUILDKIT_INDEX = (
    "sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea"
)
BUILDKIT_MANIFEST = (
    "sha256:98cc6a3fc46220d00f8224ae483f3274fc874e9be8d7dd1e2e2c5481209228b5"
)
BUILDKIT_CONFIG = (
    "sha256:27933730df224df80c41f4e5a9b33fa78831a79fd31903df3bb7deb49363422f"
)
BUILDKIT_IMAGE = "moby/buildkit@" + BUILDKIT_INDEX
ARCHIVE_LIMIT = 256 * 1024**2
GROWTH_STOP = 2 * 1024**3
OVERRIDES = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "BUILDX_CONFIG",
    "BUILDX_BUILDER",
)


class BootstrapError(RuntimeError):
    pass


FAILURE_CODES = {
    "unexpected Docker version": "ENGINE_VERSION",
    "unexpected Docker context": "ENGINE_CONTEXT",
    "unexpected Docker socket": "ENGINE_SOCKET",
    "existing Docker workloads": "ENGINE_WORKLOADS",
    "unsupported Docker service override": "SERVICE_OVERRIDE",
    "unsupported Docker service flag": "SERVICE_FLAGS",
    "unsupported Docker service endpoint": "SERVICE_ENDPOINT",
    "unsupported daemon log destination": "SERVICE_LOGS",
    "conflicting daemon configuration": "DAEMON_CONFIG",
    "containerd store not enabled": "ENGINE_STORE",
    "unexpected Buildx version": "BUILDX_VERSION",
    "unexpected running builder": "BUILDER_STATE",
    "running builder image differs from approved index graph": "BUILDER_IMAGE",
    "mutable image tag changed": "IMAGE_IDENTITY",
    "client deadline": "CLIENT_DEADLINE",
    "monitored growth STOP threshold": "GROWTH_STOP",
}


class Route(Enum):
    DELIVER = "deliver-only"
    LOAD = "load-images"
    SMOKE = "exporter-smoke-only"


def strict_json(raw: str | bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise BootstrapError("duplicate JSON field")
            result[key] = value
        return result

    def invalid(value: str) -> object:
        raise BootstrapError("nonfinite JSON value")

    if not 0 < len(raw) <= 1024**2:
        raise BootstrapError("metadata bound")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def parse_route(event: object, selected: str) -> Route:
    if not isinstance(event, dict) or event.get("inputs") != {
        "delivery_mode": selected
    }:
        raise BootstrapError("dispatch inputs differ")
    try:
        return Route(selected)
    except ValueError as exc:
        raise BootstrapError("unknown exact execution route") from exc


def route_from_env(env: Mapping[str, str]) -> Route:
    for key in stage.SECRET_NAMES:
        if env.get(key):
            raise BootstrapError("private credential in public preparation")
    event = strict_json(transfer.metadata_bytes(Path(env["GITHUB_EVENT_PATH"])))
    return parse_route(event, env["EXPORTER_ROUTE"])


def require_source(root: Path, env: Mapping[str, str]) -> Route:
    route = route_from_env(env)
    source, ref, actor = env["GITHUB_SHA"], env["GITHUB_REF"], env["GITHUB_ACTOR"]
    if (
        env.get("GITHUB_REPOSITORY") != transfer.REPOSITORY
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_JOB") != "verify"
        or env.get("GITHUB_TRIGGERING_ACTOR") != actor
        or not actor
        or actor.endswith("[bot]")
        or re.fullmatch(r"[0-9a-f]{40}", source) is None
        or re.fullmatch(
            r"refs/tags/v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", ref
        )
        is None
        or env.get("GITHUB_WORKFLOW_SHA") != source
        or env.get("GITHUB_WORKFLOW_REF")
        != transfer.REPOSITORY + "/.github/workflows/private-verification.yml@" + ref
    ):
        raise BootstrapError("noncanonical public source event")
    if route != Route.SMOKE:
        stage.require_release(root, env)
        return route
    release.validate_destination()
    release.ensure_clean_context(source)
    main, state, tags = release.fetch_remote_state()
    if state is None:
        raise BootstrapError("missing public release state")
    release.validate_state(state, tags, main)
    release.ensure_source_on_main(source, main)
    matches = [tag for tag in tags if tag.name == ref.removeprefix("refs/tags/")]
    if (
        len(matches) != 1
        or matches[0].metadata is None
        or matches[0].peeled_oid != source
    ):
        raise BootstrapError("public release tag mismatch")
    if release.read_release_target(source).text != ref.removeprefix("refs/tags/v"):
        raise BootstrapError("public release version mismatch")
    return route


class Commands:
    def __init__(
        self, root: Path, deadline: float, observe: Callable[[], None] | None = None
    ) -> None:
        self.root, self.deadline, self.observe = root, deadline, observe

    def run(self, args: list[str]) -> str:
        if time.monotonic() >= self.deadline:
            raise BootstrapError("client deadline")
        process = subprocess.Popen(
            args,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        output = bytearray()
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0:
                        raise BootstrapError("client deadline")
                    if self.observe is not None:
                        self.observe()
                    if not selector.select(min(2, remaining)):
                        continue
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > 1024**2:
                        raise BootstrapError("command output bound")
            if process.wait(timeout=max(0.001, self.deadline - time.monotonic())):
                raise BootstrapError("public preparation command failed")
            return output.decode().strip()
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def require_host(env: Mapping[str, str]) -> None:
    if any(env.get(key) for key in OVERRIDES):
        raise BootstrapError("Docker environment override")
    if any(
        env.get(key) != expected
        for key, expected in {
            "GITHUB_ACTIONS": "true",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
        }.items()
    ):
        raise BootstrapError("not supported hosted runner")


def engine_state(run: Callable[[list[str]], str]) -> str:
    if run(["docker", "context", "show"]) != "default":
        raise BootstrapError("unexpected Docker context")
    if (
        strict_json(
            run(
                [
                    "docker",
                    "context",
                    "inspect",
                    "default",
                    "--format",
                    "{{json .Endpoints.docker.Host}}",
                ]
            )
        )
        != SOCKET
    ):
        raise BootstrapError("unexpected Docker socket")
    for component in ("Client", "Server"):
        if (
            run(["docker", "version", "--format", "{{." + component + ".Version}}"])
            != ENGINE_VERSION
        ):
            raise BootstrapError("unexpected Docker version")
    driver = run(["docker", "info", "--format", "{{.Driver}}"])
    status = run(["docker", "info", "--format", "{{json .DriverStatus}}"])
    return transfer.docker_store_mode(driver, status)


def check_service(
    exec_start: str, environment: str, environment_files: str, stdout: str, stderr: str
) -> None:
    match = re.fullmatch(r"\{ path=/usr/bin/dockerd ; argv\[\]=(.*?) ;.*\}", exec_start)
    if match is None or environment or environment_files:
        raise BootstrapError("unsupported Docker service override")
    tokens = shlex.split(match[1])
    if not tokens or tokens.pop(0) != "/usr/bin/dockerd":
        raise BootstrapError("unsupported Docker service executable")
    hosts: list[str] = []
    sockets: list[str] = []
    while tokens:
        item = tokens.pop(0)
        if item in ("-H", "--host", "--containerd") and tokens:
            value = tokens.pop(0)
            (sockets if item == "--containerd" else hosts).append(value)
        elif item.startswith("--containerd="):
            sockets.append(item.split("=", 1)[1])
        elif item.startswith("--host="):
            hosts.append(item.split("=", 1)[1])
        else:
            raise BootstrapError("unsupported Docker service flag")
    if hosts != ["fd://"] or sockets not in ([], ["/run/containerd/containerd.sock"]):
        raise BootstrapError("unsupported Docker service endpoint")
    if stdout != "journal" or stderr not in ("inherit", "journal"):
        raise BootstrapError("unsupported daemon log destination")


def containerd_config(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BootstrapError("daemon configuration must be an object")
    if (
        set(value) & {"hosts", "storage-driver", "data-root", "containerd", "rootless"}
        or value.get("debug", False) is not False
    ):
        raise BootstrapError("conflicting daemon configuration")
    features = value.get("features", {})
    if not isinstance(features, dict) or any(
        type(flag) is not bool for flag in features.values()
    ):
        raise BootstrapError("invalid daemon feature configuration")
    return {**value, "features": {**features, "containerd-snapshotter": True}}


def configure_file(path: Path, *, owner: int = 0) -> None:
    parent = path.parent
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or parent.stat().st_uid != owner
        or parent.stat().st_mode & 0o022
    ):
        raise BootstrapError("unsafe daemon configuration directory")
    original: bytes | None = None
    mode = 0o644
    group = os.getgid()
    if path.is_symlink():
        raise BootstrapError("unsafe daemon configuration file")
    if path.exists():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != owner
                or info.st_mode & 0o022
                or info.st_size > 65536
            ):
                raise BootstrapError("unsafe daemon configuration file")
            mode = stat.S_IMODE(info.st_mode)
            group = info.st_gid
            original = stream.read(65537)
    current = strict_json(original) if original is not None else {}
    proposed = containerd_config(current)
    if current == proposed:
        return
    backup = parent / (".sapphire-exporter-backup-" + uuid.uuid4().hex)
    if original is not None:
        with backup.open("xb") as stream:
            stream.write(original)
        backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(prefix=".sapphire-exporter-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(proposed, sort_keys=True) + "\n").encode())
            os.fchown(stream.fileno(), owner, group)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_engine(
    root: Path, env: Mapping[str, str], run: Callable[[list[str]], str]
) -> None:
    require_host(env)
    previous_mode = engine_state(run)
    if (
        run(["docker", "info", "--format", "{{.Containers}}"]) != "0"
        or run(["docker", "info", "--format", "{{.Swarm.LocalNodeState}}"])
        != "inactive"
    ):
        raise BootstrapError("existing Docker workloads")
    fields = [
        run(["systemctl", "show", "docker.service", "--property=" + key, "--value"])
        for key in (
            "ExecStart",
            "Environment",
            "EnvironmentFiles",
            "StandardOutput",
            "StandardError",
        )
    ]
    check_service(*fields)
    run(
        [
            "sudo",
            "--non-interactive",
            sys.executable,
            "-I",
            str(root / "tools/qualify_image_exporter.py"),
            "configure",
        ]
    )
    if previous_mode != "containerd":
        run(["sudo", "--non-interactive", "systemctl", "restart", "docker.service"])
    if engine_state(run) != "containerd":
        raise BootstrapError("containerd store not enabled")


@dataclass(frozen=True, kw_only=True, slots=True)
class DiskPoint:
    device: int
    free: int


class GrowthMonitor:
    def __init__(self, owned: Path, docker_root: Path) -> None:
        self.owned = owned
        self.paths = (Path("/"), docker_root, Path("/var/lib/containerd"), owned)
        self.baseline = self.snapshot()
        self.samples: list[dict[str, object]] = []

    def snapshot(self) -> dict[str, DiskPoint | None]:
        values: dict[str, DiskPoint | None] = {}
        for path in self.paths:
            if path == Path("/var/lib/containerd") and not path.exists():
                values[str(path)] = None
                continue
            if not path.is_dir():
                raise BootstrapError("required disk observation unavailable")
            values[str(path)] = DiskPoint(
                device=path.stat().st_dev, free=shutil.disk_usage(path).free
            )
        return values

    def check(self) -> None:
        current = self.snapshot()
        growth: dict[int, int] = {}
        for name, point in current.items():
            baseline = self.baseline[name]
            if point is not None and baseline is not None:
                if point.device != baseline.device:
                    raise BootstrapError("disk observation device changed")
                growth[point.device] = max(
                    growth.get(point.device, 0), baseline.free - point.free
                )
        owned_bytes = sum(
            path.stat().st_size
            for path in self.owned.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        self.samples.append(
            {
                "paths": {
                    name: asdict(point) if point else None
                    for name, point in current.items()
                },
                "owned_bytes": owned_bytes,
            }
        )
        if (
            sum(max(0, value) for value in growth.values()) >= GROWTH_STOP
            or owned_bytes >= GROWTH_STOP
        ):
            raise BootstrapError("monitored growth STOP threshold")


def require_builder(run: Callable[[list[str]], str]) -> None:
    if (
        re.fullmatch(
            r"github.com/docker/buildx v0\.38\.0(?: .*?)?",
            run(["docker", "buildx", "version"]),
        )
        is None
    ):
        raise BootstrapError("unexpected Buildx version")
    info = run(["docker", "buildx", "inspect", BUILDER])
    for pattern in (
        r"^Driver:\s+docker-container$",
        r"^Status:\s+running$",
        r"^BuildKit version:\s+v0\.33\.1$",
        r"^Endpoint:\s+(?:unix:///var/run/docker.sock|default)$",
    ):
        if re.search(pattern, info, re.MULTILINE) is None:
            raise BootstrapError("unexpected running builder")
    container = "buildx_buildkit_" + BUILDER + "0"
    requested = run(
        ["docker", "container", "inspect", "--format", "{{.Config.Image}}", container]
    )
    image = run(["docker", "container", "inspect", "--format", "{{.Image}}", container])
    if requested != BUILDKIT_IMAGE or image not in {
        BUILDKIT_INDEX,
        BUILDKIT_MANIFEST,
        BUILDKIT_CONFIG,
    }:
        raise BootstrapError("running builder image differs from approved index graph")


def build_args(context: Path, tag: str) -> list[str]:
    return [
        "docker",
        "buildx",
        "build",
        "--builder",
        BUILDER,
        "--platform",
        "linux/amd64",
        "--file",
        str(context / "Dockerfile"),
        "--tag",
        tag,
        "--load",
        "--pull",
        "--provenance=false",
        "--sbom=false",
        "--network=none",
        str(context),
    ]


def check_public_archive(path: Path) -> None:
    with tarfile.open(path, "r:") as archive:
        members: dict[str, tarfile.TarInfo] = {}
        for member in archive:
            if (
                len(members) >= 128
                or member.name in members
                or not (member.isfile() or member.isdir())
                or member.name.startswith("/")
                or ".." in Path(member.name).parts
                or "\\" in member.name
            ):
                raise BootstrapError("unsafe or excessive public archive members")
            members[member.name] = member

        def read(name: str) -> object:
            member = members.get(name)
            if member is None or not member.isfile() or not 0 < member.size <= 1024**2:
                raise BootstrapError("public metadata size/type")
            stream = archive.extractfile(member)
            if stream is None:
                raise BootstrapError("missing public metadata")
            raw = stream.read(1024**2 + 1)
            if (
                name.startswith("blobs/sha256/")
                and hashlib.sha256(raw).hexdigest() != name[13:]
            ):
                raise BootstrapError("public metadata hash mismatch")
            return strict_json(raw)

        entries = read("manifest.json")
        if not isinstance(entries, list):
            raise BootstrapError("invalid public Docker manifest")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("Config"), str):
                raise BootstrapError("invalid public Docker config")
            read(entry["Config"])
        pending = [(read("index.json"), 0)]
        visited: set[str] = set()
        while pending:
            obj, depth = pending.pop()
            if not isinstance(obj, dict) or depth > 8:
                raise BootstrapError("public descriptor graph depth/type")
            children = obj.get("manifests", [])
            if not isinstance(children, list):
                raise BootstrapError("invalid public descriptor children")
            for child in children:
                if not isinstance(child, dict):
                    raise BootstrapError("invalid public descriptor")
                digest = transfer.image_id(child.get("digest"))
                if digest not in visited:
                    visited.add(digest)
                    if len(visited) > 16:
                        raise BootstrapError("public descriptor count")
                    pending.append((read("blobs/sha256/" + digest[7:]), depth + 1))
            if "config" in obj:
                config = obj["config"]
                if not isinstance(config, dict):
                    raise BootstrapError("invalid public config descriptor")
                digest = transfer.image_id(config.get("digest"))
                read("blobs/sha256/" + digest[7:])


def public_work(root: Path, env: Mapping[str, str]) -> Path:
    parent = Path(env["RUNNER_TEMP"])
    if not parent.is_absolute() or not parent.is_dir() or parent.is_symlink():
        raise BootstrapError("unsafe runner temporary directory")
    parent = parent.resolve()
    if parent == root or root in parent.parents or parent in root.parents:
        raise BootstrapError("public output must be outside checkout")
    return Path(tempfile.mkdtemp(prefix="exporter-smoke-", dir=parent))


def smoke(root: Path, env: Mapping[str, str], commands: Commands) -> None:
    require_host(env)
    if engine_state(commands.run) != "containerd":
        raise BootstrapError("smoke requires containerd")
    require_builder(commands.run)
    work = public_work(root, env)
    nonce = uuid.uuid4().hex
    tags = [f"exporter-smoke-{nonce}:{variant}" for variant in ("default", "aquacast")]
    ids: list[str] = []
    result: dict[str, object] = {
        "status": "HOLD",
        "tags": tags,
        "exclusive_use_attested": False,
        "daemon_quota_claimed": False,
    }
    docker_root = Path(
        commands.run(["docker", "info", "--format", "{{.DockerRootDir}}"])
    )
    if not docker_root.is_absolute():
        raise BootstrapError("invalid Docker root")
    monitor = GrowthMonitor(work, docker_root)
    commands.observe = monitor.check
    (work / "ownership.json").write_text(json.dumps({"tags": tags, "nonce": nonce}))
    try:
        for variant, tag in zip(("default", "aquacast"), tags, strict=True):
            # Query only this unpredictable owned tag; never list unrelated images.
            if commands.run(
                [
                    "docker",
                    "image",
                    "ls",
                    "--filter",
                    "reference=" + tag,
                    "--format",
                    "{{.ID}}",
                ]
            ):
                raise BootstrapError("fixture tag already exists")
            context = work / variant
            context.mkdir(mode=0o700)
            (context / "payload.txt").write_text("public shared exporter fixture\n")
            os.utime(context / "payload.txt", (0, 0))
            (context / ".dockerignore").write_text("*\n!Dockerfile\n!payload.txt\n")
            (context / "Dockerfile").write_text(
                "FROM scratch\nCOPY payload.txt /payload.txt\n"
                f'LABEL exporter.nonce="{nonce}" exporter.variant="{variant}"\n'
            )
            commands.run(build_args(context, tag))
            raw_id = transfer.image_id(
                commands.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
            )
            if (
                commands.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{.Os}}/{{.Architecture}}",
                        raw_id,
                    ]
                )
                != "linux/amd64"
            ):
                raise BootstrapError("fixture platform mismatch")
            (work / (variant + "-image-id")).write_text(raw_id + "\n")
            ids.append(
                transfer.image_id((work / (variant + "-image-id")).read_text().strip())
            )
            if (
                commands.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
                != raw_id
            ):
                raise BootstrapError("mutable image tag changed")
        if len(set(ids)) != 2:
            raise BootstrapError("fixture identities must differ")
        monitor.check()
        archive = work / "images.tar"
        remaining = commands.deadline - time.monotonic() - 30
        if remaining <= 0:
            raise BootstrapError("insufficient export client budget")
        stage.archive_images(
            ids,
            archive,
            ARCHIVE_LIMIT,
            work / "save.log",
            timeout_seconds=remaining,
            observer=monitor.check,
        )
        monitor.check()
        check_public_archive(archive)
        stage.require_untagged_pair(archive)
        mode = engine_state(commands.run)
        if mode != "containerd":
            raise BootstrapError("exporter store changed")
        archive_tool = transfer.tool("image_archive")
        bindings = []
        for variant, tag, raw_id in zip(
            ("default", "aquacast"), tags, ids, strict=True
        ):
            if (
                commands.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
                != raw_id
            ):
                raise BootstrapError("mutable image tag changed")
            observed = archive_tool.describe_image_archive(archive, raw_id)
            if (
                observed.labels.get("exporter.nonce") != nonce
                or observed.labels.get("exporter.variant") != variant
            ):
                raise BootstrapError("fixture label mismatch")
            bindings.append(
                transfer.ImageBinding(
                    variant=variant,
                    raw_id=raw_id,
                    config_digest=observed.config_digest,
                    platform=observed.platform,
                    store_mode=mode,
                    id_kind="config"
                    if observed.config_digest == raw_id
                    else "oci-descriptor",
                )
            )
            if (
                commands.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
                != raw_id
            ):
                raise BootstrapError("mutable image tag changed")
        if len({item.config_digest for item in bindings}) != 2:
            raise BootstrapError("fixture configs must differ")
        result["images"] = [asdict(item) for item in bindings]
        result["unpacked_layer_bytes"] = archive_tool.unpacked_layer_bytes(
            archive, ARCHIVE_LIMIT
        )
        result["archive_bytes"] = archive.stat().st_size
        result["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        removed: list[str] = []
        result["removed_tags"] = removed
        for tag in tags:
            commands.run(["docker", "image", "rm", tag])
            removed.append(tag)
        for variant in ("default", "aquacast"):
            shutil.rmtree(work / variant)
        archive.unlink()
        result["status"] = "PASS"
    finally:
        result["samples"] = monitor.samples
        result["residue_policy"] = (
            "Only exact owned tags removed on success; no prune; "
            "failure may retain images/cache"
        )
        (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("route", "engine", "smoke", "configure"))
    phase = parser.parse_args().phase
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    try:
        if phase == "configure":
            if os.geteuid() != 0:
                raise BootstrapError("daemon configuration requires root")
            configure_file(Path("/etc/docker/daemon.json"))
        elif phase == "route":
            require_source(root, os.environ)
        else:
            route_from_env(os.environ)
            commands = Commands(
                root, time.monotonic() + (150 if phase == "engine" else 270)
            )
            if phase == "engine":
                prepare_engine(root, os.environ, commands.run)
            else:
                smoke(root, os.environ, commands)
    except (Exception, KeyboardInterrupt) as error:
        code = (
            FAILURE_CODES.get(str(error), "PUBLIC_PREPARATION")
            if isinstance(error, BootstrapError)
            else "PUBLIC_PREPARATION"
        )
        os.write(2, f"EXPORTER_PREPARATION_FAILED {phase.upper()} {code}\n".encode())
        return 1
    os.write(1, b"EXPORTER_PREPARATION_PASSED\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
